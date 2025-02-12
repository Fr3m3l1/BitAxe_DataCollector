import os
import requests
import sys
import time
import signal
from dotenv import load_dotenv

def main():
    # Read environment variables
    system_ip = os.getenv('SYSTEM_IP')

    print(f"SYSTEM_IP: {system_ip}")

    # Validate required environment variables
    required_env_vars = {
        'SYSTEM_IP': system_ip,
    }
    for var, value in required_env_vars.items():
        if not value:
            print(f"Error: {var} environment variable is not set", file=sys.stderr)
            sys.exit(1)

    # Turn on = 1, Turn off = 0
    state_on = 1

    source_url = f"http://{system_ip}/elay?state={state_on}"

    # Set up signal handler for graceful exit
    def signal_handler(sig, frame):
        print("\nExiting gracefully...")
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    while True:
        # Fetch data
        try:
            response = requests.get(source_url, timeout=10)
            response.raise_for_status()
            data = response.json()
            print(f"Received data: {data}")
        except requests.exceptions.RequestException as e:
            print(f"Error fetching data from source: {e}", file=sys.stderr)
        except ValueError as e:
            print(f"Error parsing JSON response: {e}", file=sys.stderr)

        # Wait for the next interval
        time.sleep(10)

if __name__ == "__main__":
    # load environment variables from .env file
    load_dotenv()
    main()
    