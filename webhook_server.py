import logging
import os
import json
import pickle
from typing import Dict, Any
from datetime import datetime, timedelta
import asyncio

from fastapi import FastAPI, Request, HTTPException
import uvicorn
from dotenv import load_dotenv
from aiogram import Bot

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG,
    filename="/opt/telegram-yookassa-bot/webhook.log"
)
logger = logging.getLogger(__name__)

# Get environment variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
bot = Bot(token=TELEGRAM_TOKEN)

# Load data from files
def load_data():
    global payment_storage, user_subscriptions
    
    try:
        with open('/opt/telegram-yookassa-bot/payment_storage.pkl', 'rb') as f:
            payment_storage = pickle.load(f)
            logger.info(f"Loaded {len(payment_storage)} payments from storage")
            logger.debug(f"Payment IDs in storage: {list(payment_storage.keys())}")
    except FileNotFoundError:
        payment_storage = {}
        logger.info("Created new payment storage")
    
    try:
        with open('/opt/telegram-yookassa-bot/user_subscriptions.pkl', 'rb') as f:
            user_subscriptions = pickle.load(f)
            logger.info(f"Loaded {len(user_subscriptions)} subscriptions from storage")
    except FileNotFoundError:
        user_subscriptions = {}
        logger.info("Created new user subscriptions storage")

def save_data():
    with open('/opt/telegram-yookassa-bot/payment_storage.pkl', 'wb') as f:
        pickle.dump(payment_storage, f)
    
    with open('/opt/telegram-yookassa-bot/user_subscriptions.pkl', 'wb') as f:
        pickle.dump(user_subscriptions, f)
    
    logger.info("Data saved to files")

# Load data on startup
load_data()

# Create FastAPI app
app = FastAPI()

async def process_successful_payment(payment_data: Dict[str, Any]) -> bool:
    """Process a successful payment from YooKassa webhook."""
    logger.debug(f"Processing payment data: {json.dumps(payment_data)}")
    
    payment_id = payment_data.get("object", {}).get("id")
    
    if not payment_id:
        logger.warning("No payment ID found in the webhook data")
        return False
    
    # Reload data to get the latest updates
    load_data()
    
    logger.debug(f"Payment ID from webhook: {payment_id}")
    logger.debug(f"Available payment IDs in storage: {list(payment_storage.keys())}")
    
    # If the payment is not in storage but contains valid metadata, we'll create it
    if payment_id not in payment_storage:
        # Try to get user data from metadata
        metadata = payment_data.get("object", {}).get("metadata", {})
        user_id = metadata.get("user_id")
        subscription_id = metadata.get("subscription_id")
        days = metadata.get("days")
        
        if user_id and subscription_id and days:
            logger.info(f"Payment {payment_id} not found in storage, but contains valid metadata. Creating entry.")
            payment_storage[payment_id] = {
                "user_id": int(user_id),
                "subscription_id": subscription_id,
                "subscription_days": int(days),
                "status": "pending",
                "created_at": datetime.now().isoformat(),
            }
            save_data()
        else:
            logger.warning(f"Unknown payment ID received: {payment_id} and no valid metadata found")
            return False
    
    # Get stored payment data
    stored_payment = payment_storage[payment_id]
    user_id = stored_payment["user_id"]
    days = stored_payment["subscription_days"]
    
    # Check if payment is successful
    status = payment_data.get("object", {}).get("status")
    logger.debug(f"Payment status: {status}")
    
    if status != "succeeded":
        logger.info(f"Payment {payment_id} status: {status}, not processing")
        stored_payment["status"] = status
        save_data()
        return True
    
    # If payment is already processed, don't process it again
    if stored_payment.get("status") == "succeeded" and stored_payment.get("processed", False):
        logger.info(f"Payment {payment_id} already processed, skipping")
        return True
    
    # Update payment status
    stored_payment["status"] = "succeeded"
    stored_payment["processed"] = True
    
    # Calculate subscription end date
    now = datetime.now()
    
    # If user already has a subscription, extend it
    if user_id in user_subscriptions:
        current_end = datetime.fromisoformat(user_subscriptions[user_id]["end_date"])
        if current_end > now:
            end_date = current_end + timedelta(days=days)
        else:
            end_date = now + timedelta(days=days)
    else:
        end_date = now + timedelta(days=days)
    
    # Update user subscription
    user_subscriptions[user_id] = {"end_date": end_date.isoformat()}
    
    # Save the updated data
    save_data()
    
    logger.info(f"Successfully processed payment {payment_id} for user {user_id}")
    logger.info(f"User {user_id} subscription extended until {end_date.isoformat()}")
    
    # Notify user about successful payment
    try:
        logger.debug(f"Attempting to send notification to user {user_id}")
        await bot.send_message(
            chat_id=int(user_id),  # Преобразуем в int, если user_id хранится как строка
            text=f"Ваш платеж успешно обработан!\n"
                f"Подписка активна до {end_date.strftime('%d.%m.%Y')}"
        )
        logger.info(f"Sent payment confirmation to user {user_id}")
    except Exception as e:
        logger.error(f"Error sending payment confirmation to user {user_id}: {e}")
    
    return True

@app.post("/webhook")
async def yookassa_webhook(request: Request):
    """Handle YooKassa webhook notifications."""
    logger.info("Received webhook request")
    
    # According to YooKassa documentation, we need to return 200 OK
    # even if there's an error, to prevent retries
    
    # Log request headers for debugging
    headers = dict(request.headers.items())
    logger.debug(f"Request headers: {json.dumps(headers)}")
    
    try:
        # Get request body
        body = await request.body()
        logger.debug(f"Raw request body: {body.decode('utf-8')}")
        
        # Parse JSON
        data = await request.json()
        logger.info(f"Received webhook data: {json.dumps(data)}")
        
        # Verify the event type (payment.succeeded, payment.canceled, etc.)
        event = data.get("event")
        if not event:
            logger.warning("No event in webhook data")
            return {"status": "success"}  # Still return 200 OK
        
        logger.info(f"Event type: {event}")
        
        # Process payment.succeeded events
        if event == "payment.succeeded":
            logger.info("Processing payment.succeeded event")
            await process_successful_payment(data)
        elif event == "payment.waiting_for_capture":
            logger.info("Processing payment.waiting_for_capture event")
            # For testing, we can also process this event
            await process_successful_payment(data)
        else:
            logger.info(f"Received {event} event, not processing")
        
        # Always return 200 OK to acknowledge the webhook
        return {"status": "success"}
    
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON: {e}")
        return {"status": "success"}  # Still return 200 OK
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        # Still return 200 OK to prevent retries
        return {"status": "success"}

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

# Test endpoint for debugging
@app.get("/test-webhook/{payment_id}")
async def test_webhook(payment_id: str):
    """Test endpoint to simulate a webhook call."""
    # Reload data to get the latest updates
    load_data()
    
    if payment_id not in payment_storage:
        return {"status": "error", "message": f"Payment {payment_id} not found"}
    
    # Create a test webhook payload
    test_data = {
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "metadata": payment_storage[payment_id].get("metadata", {})
        }
    }
    
    # Process the test webhook
    success = await process_successful_payment(test_data)
    
    if success:
        return {"status": "success", "message": f"Successfully processed test webhook for payment {payment_id}"}
    else:
        return {"status": "error", "message": f"Failed to process test webhook for payment {payment_id}"}

# Display storage contents for debugging
@app.get("/debug/storage")
async def debug_storage():
    """Display storage contents for debugging."""
    load_data()
    return {
        "payment_storage": payment_storage,
        "user_subscriptions": user_subscriptions
    }

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
