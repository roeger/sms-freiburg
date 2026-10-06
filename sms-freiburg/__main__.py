import argparse
import json
import logging
import paho.mqtt.client as mqtt
import sys
import yaml
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path


from .client import SMSFreiburgClient, LoginError

@dataclass
class Config:
    user: str
    password: str
    mqtt_user: str
    mqtt_password: str
    mqtt_ip: str
    mqtt_port: int
    topic: str
    verbose: bool


def load_config(path: Path, profile=""):
    try:
        with path.open() as f:
            configs = yaml.safe_load(f)
    except FileNotFoundError:
        logger = logging.getLogger(__name__)
        logger.info(f"Could not find config file at {path}, only using command line arguments.")
        return {}

    try:
        values = configs.get("defaults", {})
        if profile:
            values.update(configs[profile])
    except KeyError:
        available = ", ".join(configs)
        raise ValueError(
            f"Unknown profile {profile!r}. Available profiles: {available}"
        )
    return values


def parse_configuration():
    # parse command line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--user", help="'Benutzername' on sms-freiburg")
    parser.add_argument("--password", help="Password on sms-freiburg")
    parser.add_argument("--mqtt_user", help="User for authentication at MQTT broker")
    parser.add_argument("--mqtt_password", help="Password for authentication at MQTT broker")
    parser.add_argument("--topic", help="MQTT topic for the message")
    parser.add_argument("--mqtt_ip", help="IP of the MQTT broker")
    parser.add_argument("--mqtt_port", help="Port of the MQTT broker")
    parser.add_argument("--profile", help="Profile from configuration file")
    parser.add_argument("-v", "--verbose", help="verbose", action="store_true")
    args= parser.parse_args()
    
    logging.basicConfig(
            level=logging.DEBUG if args.verbose else logging.INFO,
            format="%(levelname)s: %(message)s")

    # parse config file
    values = load_config(args.config, args.profile)
    
    # combine command line arguments with values from configuration file
    # (command line overrides config file).
    arg_vals = {key: value for key, value in vars(args).items()
                if value is not None
                and key not in {"config", "profile"}}
    values.update(arg_vals)

    # aim for clean error messages if something is missing:
    required = {f.name for f in fields(Config)}
    missing = required - values.keys()

    if missing:
        parser.error( "Missing required configuration values: " +
                      ", ".join(sorted(missing)))

    return Config(**values)


def main():
    config = parse_configuration()

    logger = logging.getLogger(__name__)

    logger.debug(f"Using {config}")

    c = SMSFreiburgClient(config.user, config.password)
    try:
        all_meals = c.get_data()
    except LoginError:
        sys.exit(1)

    result = {
        "updated": datetime.now().astimezone().isoformat(),
        "meals": [
            {
                "date": datetime.strptime(date, "%d.%m.%y").date().isoformat(),
                "ordered": quantity,
                "meal": meal.strip('"').strip(),
            }
            for date, (quantity, meal) in all_meals.items()
        ],
    }


    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.username_pw_set(config.mqtt_user, config.mqtt_password)
    mqtt_client.connect(config.mqtt_ip, config.mqtt_port, 60)

    mqtt_client.publish(
        config.topic,
        json.dumps(result, ensure_ascii=False),
        retain=True
    )

    mqtt_client.disconnect()


if __name__ == "__main__":
    main()
