import os
import requests
import sys
import time
import signal

def main():
    # Read environment variables
    system_ip = os.getenv('SYSTEM_IP')
    target_url = os.getenv('TARGET_URL')
    interval = os.getenv('INTERVAL')

    # Validate required environment variables
    required_env_vars = {
        'SYSTEM_IP': system_ip,
        'TARGET_URL': target_url,
        'INTERVAL': interval,
    }
    for var, value in required_env_vars.items():
        if not value:
            print(f"Error: {var} environment variable is not set", file=sys.stderr)
            sys.exit(1)

    # Parse and validate interval
    try:
        interval_minutes = int(interval)
    except ValueError:
        print(f"Error: INTERVAL must be an integer (minutes), got '{interval}'", file=sys.stderr)
        sys.exit(1)
    
    if interval_minutes <= 0:
        print(f"Error: INTERVAL must be a positive integer, got {interval_minutes}", file=sys.stderr)
        sys.exit(1)

    interval_seconds = interval_minutes * 60
    source_url = f"http://{system_ip}/api/system/info"

    # Set up signal handler for graceful exit
    def signal_handler(sig, frame):
        print("\nExiting gracefully...")
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print(f"Starting data forwarder with interval {interval_minutes} minutes")

    while True:
        # Fetch data
        try:
            response = requests.get(source_url, timeout=10)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching data from source: {e}", file=sys.stderr)
        except ValueError as e:
            print(f"Error parsing JSON response: {e}", file=sys.stderr)
        else:
            # Forward data
            try:
                post_response = requests.post(
                    target_url,
                    json=data,
                    timeout=10
                )
                post_response.raise_for_status()
            except requests.exceptions.RequestException as e:
                print(f"Error forwarding data to target: {e}", file=sys.stderr)
            else:
                print("Data successfully forwarded to target URL")

        # Wait for the next interval
        time.sleep(interval_seconds)

if __name__ == "__main__":
    main()