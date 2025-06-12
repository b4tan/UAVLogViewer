import os
import logging
from pathlib import Path
from pymavlink import mavutil
from typing import Dict, List, Any, Optional
import json
import time
import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MAVLinkParser:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.messages = {}
        self.metadata = {
            "file_name": file_path.name,
            "file_size": os.path.getsize(file_path),
            "message_count": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "corrupted_messages": 0
        }
        self.message_types = set()
        self.current_timestamp = None
        
    def _get_timestamp(self, msg) -> float:
        """Get timestamp from message, with fallbacks."""
        try:
            # Try different timestamp fields
            if hasattr(msg, '_timestamp'):
                return msg._timestamp
            elif hasattr(msg, 'time_boot_ms'):
                return msg.time_boot_ms / 1000.0  # Convert to seconds
            elif hasattr(msg, 'time_unix_usec'):
                return msg.time_unix_usec / 1000000.0  # Convert to seconds
            else:
                # If no timestamp found, use current time
                if self.current_timestamp is None:
                    self.current_timestamp = time.time()
                return self.current_timestamp
        except Exception as e:
            logger.warning(f"Error getting timestamp from message: {e}")
            return time.time()
        
    def parse(self) -> Dict[str, Any]:
        """Parse the MAVLink log file and return processed data with high-level info."""
        try:
            # Handle both .bin and .tlog files
            if self.file_path.suffix.lower() == '.tlog':
                mlog = mavutil.mavlink_connection(str(self.file_path), dialect='ardupilotmega')
            else:
                mlog = mavutil.mavlink_connection(str(self.file_path))

            logger.info(f"Processing file: {self.file_path}")

            # Initialize trajectory data
            trajectory = []
            time_trajectory = {}
            start_altitude = None
            last_position = None
            last_timestamp = None

            # Process messages
            while True:
                try:
                    msg = mlog.recv_match()
                    if msg is None:
                        break

                    self.metadata["message_count"] += 1
                    timestamp = self._get_timestamp(msg)
                    
                    if self.metadata["first_timestamp"] is None:
                        self.metadata["first_timestamp"] = timestamp
                    self.metadata["last_timestamp"] = timestamp

                    msg_type = msg.get_type()
                    self.message_types.add(msg_type)
                    if msg_type not in self.messages:
                        self.messages[msg_type] = []
                    
                    # Convert message to dict and add timestamp
                    msg_dict = msg.to_dict()
                    msg_dict['_timestamp'] = timestamp
                    self.messages[msg_type].append(msg_dict)

                    # Process position data for trajectory
                    if msg_type == 'GLOBAL_POSITION_INT':
                        lat = msg.lat / 1e7  # Convert from int to degrees
                        lon = msg.lon / 1e7  # Convert from int to degrees
                        alt = msg.alt / 1000.0  # Convert from mm to meters
                        relative_alt = msg.relative_alt / 1000.0  # Convert from mm to meters
                        
                        # Only add point if position has changed significantly and enough time has passed
                        if last_timestamp is None or (timestamp - last_timestamp) >= 200:  # 200ms minimum between points
                            if last_position is None or (
                                abs(lat - last_position[0]) > 0.00001 or  # ~1m at equator
                                abs(lon - last_position[1]) > 0.00001 or
                                abs(relative_alt - last_position[2]) > 0.1  # 0.1m
                            ):
                                if start_altitude is None:
                                    start_altitude = relative_alt
                                
                                # Add to trajectory with relative altitude
                                trajectory.append([
                                    lon,
                                    lat,
                                    relative_alt - start_altitude,
                                    timestamp * 1000  # Convert to milliseconds for visualization
                                ])
                                
                                # Add to time trajectory with absolute altitude
                                time_trajectory[timestamp * 1000] = [
                                    lon,
                                    lat,
                                    relative_alt,
                                    timestamp * 1000
                                ]
                                
                                last_position = (lat, lon, relative_alt)
                                last_timestamp = timestamp
                    
                except Exception as e:
                    self.metadata["corrupted_messages"] += 1
                    logger.warning(f"Error processing message: {e}")
                    continue

            if self.metadata["first_timestamp"] and self.metadata["last_timestamp"]:
                self.metadata["duration"] = self.metadata["last_timestamp"] - self.metadata["first_timestamp"]

            # Add summary of corrupted messages to metadata
            if self.metadata["corrupted_messages"] > 0:
                logger.warning(f"Found {self.metadata['corrupted_messages']} corrupted messages in the log file")

            # Process high-level data
            attitude = self._process_attitude()
            flight_modes = self._process_flight_modes()
            vehicle_type = self._get_vehicle_type()

            # Format trajectory data for visualization
            trajectory_data = {
                "GLOBAL_POSITION_INT": {
                    "startAltitude": start_altitude,
                    "trajectory": trajectory,
                    "timeTrajectory": time_trajectory
                }
            }

            # Add trajectory data to messages for compatibility
            if "GLOBAL_POSITION_INT" not in self.messages:
                self.messages["GLOBAL_POSITION_INT"] = []
            
            # Add trajectory data to metadata
            self.metadata["trajectory_data"] = trajectory_data
            self.metadata["currentTrajectory"] = trajectory

            # Add trajectory sources to metadata
            self.metadata["trajectorySources"] = ["GLOBAL_POSITION_INT"]

            return {
                "messages": self.messages,
                "metadata": self.metadata,
                "trajectory_data": trajectory_data,
                "attitude": attitude,
                "flight_modes": flight_modes,
                "vehicle_type": vehicle_type,
                "types": list(self.message_types)  # Add message types to response
            }

        except Exception as e:
            logger.error(f"Error parsing file: {e}", exc_info=True)
            raise

    def _process_attitude(self) -> Dict[str, List[float]]:
        """Process attitude data from messages."""
        attitude = {
            "roll": [],
            "pitch": [],
            "yaw": [],
            "timestamps": []
        }
        
        if "ATTITUDE" in self.messages:
            for msg in self.messages["ATTITUDE"]:
                attitude["roll"].append(msg["roll"])
                attitude["pitch"].append(msg["pitch"])
                attitude["yaw"].append(msg["yaw"])
                attitude["timestamps"].append(msg["_timestamp"])
                
        return attitude

    def _process_flight_modes(self) -> List[Dict[str, Any]]:
        """Process flight mode changes."""
        flight_modes = []
        if "HEARTBEAT" in self.messages:
            for msg in self.messages["HEARTBEAT"]:
                if "custom_mode" in msg:
                    flight_modes.append({
                        "timestamp": msg["_timestamp"],
                        "mode": msg["custom_mode"]
                    })
        return flight_modes

    def _get_vehicle_type(self) -> str:
        """Get vehicle type from heartbeat messages."""
        vehicle_type = "UNKNOWN"
        
        # First try to get from HEARTBEAT messages
        if "HEARTBEAT" in self.messages and self.messages["HEARTBEAT"]:
            type_id = self.messages["HEARTBEAT"][0].get("type")
            if type_id is not None:
                # Map numeric type to descriptive name
                type_map = {
                    0: "Generic",
                    1: "Fixed Wing",
                    2: "Quadcopter",
                    3: "Coaxial Helicopter",
                    4: "Helicopter",
                    5: "Antenna Tracker",
                    6: "GCS",
                    7: "Airship",
                    8: "Free Balloon",
                    9: "Rocket",
                    10: "Ground Rover",
                    11: "Surface Boat",
                    12: "Submarine",
                    13: "Hexacopter",
                    14: "Octocopter",
                    15: "Tricopter",
                    16: "Flapping Wing",
                    17: "Kite",
                    18: "Onboard Controller",
                    19: "VTOL Duorotor",
                    20: "VTOL Quadrotor",
                    21: "VTOL Tiltrotor",
                    22: "VTOL Reserved 2",
                    23: "VTOL Reserved 3",
                    24: "VTOL Reserved 4",
                    25: "VTOL Reserved 5",
                    26: "Gimbal",
                    27: "ADSB",
                    28: "Parafoil",
                    29: "Dodecarotor"
                }
                vehicle_type = type_map.get(type_id, f"Unknown Type {type_id}")
        
        # If still unknown, try to get from MSG messages
        if vehicle_type == "UNKNOWN" and "MSG" in self.messages:
            for msg in self.messages["MSG"]:
                msg_text = msg.get("Message", "").lower()
                if "arduplane" in msg_text:
                    vehicle_type = "Fixed Wing"
                    break
                elif "arducopter" in msg_text:
                    vehicle_type = "Quadcopter"
                    break
                elif "ardusub" in msg_text:
                    vehicle_type = "Submarine"
                    break
                elif "rover" in msg_text:
                    vehicle_type = "Ground Rover"
                    break
                elif "tracker" in msg_text:
                    vehicle_type = "Antenna Tracker"
                    break
        
        return vehicle_type

    def get_datetime_from_timestamp(self, timestamp: float) -> str:
        """Convert a timestamp to a datetime string.
        
        Args:
            timestamp: The timestamp in seconds (can be boot time or unix time)
            
        Returns:
            str: Formatted datetime string in ISO format
        """
        try:
            # If timestamp is very small (less than 1000000000), assume it's boot time
            # and convert to unix time by adding the first timestamp
            if timestamp < 1000000000 and self.metadata["first_timestamp"] is not None:
                unix_timestamp = self.metadata["first_timestamp"] + timestamp
            else:
                unix_timestamp = timestamp
                
            # Convert to datetime and format
            dt = datetime.datetime.fromtimestamp(unix_timestamp)
            return dt.isoformat()
        except Exception as e:
            logger.error(f"Error converting timestamp {timestamp}: {e}")
            return "Invalid timestamp"

    def get_message_summary(self) -> Dict[str, int]:
        """Get a summary of message types and their counts."""
        return {msg_type: len(msgs) for msg_type, msgs in self.messages.items()}
        
    def get_telemetry_data(self) -> Dict[str, Any]:
        """Extract key telemetry data from messages."""
        telemetry = {
            "attitude": [],
            "global_position": [],
            "battery_status": [],
            "system_status": [],
            "heartbeat": []
        }
        
        # Extract relevant messages
        for msg_type, messages in self.messages.items():
            if msg_type == "ATTITUDE":
                telemetry["attitude"].extend(messages)
            elif msg_type == "GLOBAL_POSITION_INT":
                telemetry["global_position"].extend(messages)
            elif msg_type == "BATTERY_STATUS":
                telemetry["battery_status"].extend(messages)
            elif msg_type == "SYS_STATUS":
                telemetry["system_status"].extend(messages)
            elif msg_type == "HEARTBEAT":
                telemetry["heartbeat"].extend(messages)
                
        return telemetry 