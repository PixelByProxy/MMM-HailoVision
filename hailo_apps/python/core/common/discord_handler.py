# region imports
# Standard library imports
import json
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# endregion imports


class DiscordHandler:
    def __init__(self, token, channel_id):
        """Initialize the DiscordHandler.

        A Discord bot token and channel ID are required. The bot must have
        permission to send messages in the configured channel.
        """
        self.token = token
        self.channel_id = channel_id
        self.ids_msg_sent = {}  # Dictionary to store the last sent time for each person

        if not token or not channel_id:
            raise ValueError("Discord token and channel ID must be provided.")

    def should_send_notification(self, global_id):
        """Check if a notification should be sent for the given global_id."""
        current_time = datetime.now()
        last_sent_time = self.ids_msg_sent.get(global_id)

        # Send notification if it has never been sent or if more than 1 hour has passed
        if last_sent_time is None or current_time - last_sent_time > timedelta(hours=1):
            self.ids_msg_sent[global_id] = current_time
            return True
        return False

    def send_message(self, message):
        """Post a plain text message to the configured Discord channel."""
        if not message:
            raise ValueError("Discord message must not be empty.")

        url = f"https://discord.com/api/v10/channels/{self.channel_id}/messages"
        payload = json.dumps({"content": message}).encode("utf-8")
        request = Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "hailo-apps-discord-handler",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            print(f"Error sending Discord message: HTTP {e.code} - {error_body}")
        except URLError as e:
            print(f"Error sending Discord message: {e.reason!s}")
        except Exception as e:
            print(f"Error sending Discord message: {e!s}")
        return None

    def send_notification(self, name, global_id, confidence, frame=None):
        """Send a face-recognition style notification via Discord."""
        if not name:
            message = "Unknown person detected!"
        elif name == "Unknown":
            message = f"Detected {global_id} (confidence: {confidence:.2f})"
        else:
            message = f"Detected {name} (confidence: {confidence:.2f})"

        return self.send_message(message)
