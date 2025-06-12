import os
import logging
from pathlib import Path
from pymavlink import mavutil
from typing import Dict, List, Any, Optional
import json

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
        self.metadata = {}
        self.message_types = set()
        
    def parse(self) -> Dict[str, Any]:
        """Parse the MAVLink log file and return processed data with high-level info."""
        try:
            mlog = mavutil.mavlink_connection(str(self.file_path))
            logger.info(f"Processing file: {self.file_path}")

            self.messages = {}
            self.metadata = {
                "file_name": self.file_path.name,
                "file_size": os.path.getsize(self.file_path),
                "message_count": 0,
                "first_timestamp": None,
                "last_timestamp": None
            }

            # Process messages
            while True:
                try:
                    msg = mlog.recv_match()
                    if msg is None:
                        break

                    self.metadata["message_count"] += 1
                    if self.metadata["first_timestamp"] is None:
                        self.metadata["first_timestamp"] = msg._timestamp
                    self.metadata["last_timestamp"] = msg._timestamp

                    msg_type = msg.get_type()
                    self.message_types.add(msg_type)
                    if msg_type not in self.messages:
                        self.messages[msg_type] = []
                    msg_dict = msg.to_dict()
                    self.messages[msg_type].append(msg_dict)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    continue

            if self.metadata["first_timestamp"] and self.metadata["last_timestamp"]:
                self.metadata["duration"] = self.metadata["last_timestamp"] - self.metadata["first_timestamp"]

            # --- High-level extractions below ---

            # Attitude (roll, pitch, yaw)
            attitude = {}
            if "ATTITUDE" in self.messages:
                for msg in self.messages["ATTITUDE"]:
                    t = msg.get("time_boot_ms")
                    attitude[t] = [msg.get("roll"), msg.get("pitch"), msg.get("yaw")]

            # Flight modes (mode changes)
            flight_modes = []
            if "HEARTBEAT" in self.messages:
                last_mode = None
                for msg in self.messages["HEARTBEAT"]:
                    t = msg.get("time_boot_ms")
                    mode = msg.get("custom_mode", "Unknown")
                    if last_mode != mode:
                        flight_modes.append([t, mode])
                        last_mode = mode

            # Events (arming/disarming)
            events = []
            if "HEARTBEAT" in self.messages:
                last_event = None
                for msg in self.messages["HEARTBEAT"]:
                    t = msg.get("time_boot_ms")
                    event = "Armed" if (msg.get("base_mode", 0) & 0b10000000) else "Disarmed"
                    if last_event != event:
                        events.append([t, event])
                        last_event = event

            # Mission waypoints
            mission = []
            if "CMD" in self.messages:
                for msg in self.messages["CMD"]:
                    if msg.get("Lat", 0) != 0:
                        lat = msg.get("Lat")
                        lon = msg.get("Lng")
                        alt = msg.get("Alt")
                        mission.append([lon, lat, alt])

            # Params (and their changes)
            params = []
            if "PARAM_VALUE" in self.messages:
                for msg in self.messages["PARAM_VALUE"]:
                    t = msg.get("time_boot_ms")
                    name = msg.get("param_id", "")
                    value = msg.get("param_value")
                    params.append([t, name, value])

            # Text messages (STATUSTEXT, MSG)
            text_messages = []
            if "STATUSTEXT" in self.messages:
                for msg in self.messages["STATUSTEXT"]:
                    text_messages.append([msg.get("time_boot_ms"), msg.get("severity"), msg.get("text")])
            if "MSG" in self.messages:
                for msg in self.messages["MSG"]:
                    text_messages.append([msg.get("time_boot_ms"), 0, msg.get("Message")])

            # Named value floats
            named_value_float_names = []
            if "NAMED_VALUE_FLOAT" in self.messages:
                named_value_float_names = list(set(msg.get("name") for msg in self.messages["NAMED_VALUE_FLOAT"] if "name" in msg))

            # Start time
            start_time = None
            if "SYSTEM_TIME" in self.messages:
                for msg in self.messages["SYSTEM_TIME"]:
                    if "time_unix_usec" in msg:
                        start_time = msg["time_unix_usec"]
                        break

            # Vehicle type (from HEARTBEAT)
            vehicle_type = None
            if "HEARTBEAT" in self.messages:
                for msg in self.messages["HEARTBEAT"]:
                    if "type" in msg:
                        vehicle_type = msg["type"]
                        break

            # Trajectory sources (example: GLOBAL_POSITION_INT, GPS_RAW_INT, etc.)
            trajectory_sources = []
            for src in ["GLOBAL_POSITION_INT", "GPS_RAW_INT", "AHRS2", "AHRS3"]:
                if src in self.messages:
                    trajectory_sources.append(src)

            # Attitude sources
            attitude_sources = []
            if "ATTITUDE" in self.messages:
                attitude_sources.append("ATTITUDE")
            # (Add more if you support quaternions, etc.)

            # Trajectory data for each source
            trajectory_data = {}
            if "GLOBAL_POSITION_INT" in self.messages:
                gps = self.messages["GLOBAL_POSITION_INT"]
                trajectory = []
                timeTrajectory = {}
                startAltitude = None
                for msg in gps:
                    lat = msg.get("lat")
                    lon = msg.get("lon")
                    rel_alt = msg.get("relative_alt")
                    t = msg.get("time_boot_ms")
                    if lat is not None and lon is not None and rel_alt is not None and t is not None:
                        if startAltitude is None:
                            startAltitude = rel_alt
                        trajectory.append([lon, lat, rel_alt - startAltitude, t])
                        timeTrajectory[t] = [lon, lat, rel_alt, t]
                if trajectory:
                    trajectory_data["GLOBAL_POSITION_INT"] = {
                        "startAltitude": startAltitude,
                        "trajectory": trajectory,
                        "timeTrajectory": timeTrajectory
                    }
            if "GPS_RAW_INT" in self.messages:
                gps = self.messages["GPS_RAW_INT"]
                trajectory = []
                timeTrajectory = {}
                startAltitude = None
                for msg in gps:
                    lat = msg.get("lat")
                    lon = msg.get("lon")
                    alt = msg.get("alt")
                    t = msg.get("time_boot_ms")
                    if lat is not None and lon is not None and alt is not None and t is not None:
                        if startAltitude is None:
                            startAltitude = alt / 1000.0
                        trajectory.append([lon * 1e-7, lat * 1e-7, alt / 1000.0 - startAltitude, t])
                        timeTrajectory[t] = [lon * 1e-7, lat * 1e-7, alt / 1000.0, t]
                if trajectory:
                    trajectory_data["GPS_RAW_INT"] = {
                        "startAltitude": startAltitude,
                        "trajectory": trajectory,
                        "timeTrajectory": timeTrajectory
                    }
            if "AHRS2" in self.messages:
                gps = self.messages["AHRS2"]
                trajectory = []
                timeTrajectory = {}
                startAltitude = None
                for msg in gps:
                    lat = msg.get("lat")
                    lon = msg.get("lng")
                    alt = msg.get("altitude")
                    t = msg.get("time_boot_ms")
                    if lat is not None and lon is not None and alt is not None and t is not None:
                        if startAltitude is None:
                            startAltitude = alt
                        trajectory.append([lon * 1e-7, lat * 1e-7, alt - startAltitude, t])
                        timeTrajectory[t] = [lon * 1e-7, lat * 1e-7, alt, t]
                if trajectory:
                    trajectory_data["AHRS2"] = {
                        "startAltitude": startAltitude,
                        "trajectory": trajectory,
                        "timeTrajectory": timeTrajectory
                    }
            if "AHRS3" in self.messages:
                gps = self.messages["AHRS3"]
                trajectory = []
                timeTrajectory = {}
                startAltitude = None
                for msg in gps:
                    lat = msg.get("lat")
                    lon = msg.get("lng")
                    alt = msg.get("altitude")
                    t = msg.get("time_boot_ms")
                    if lat is not None and lon is not None and alt is not None and t is not None:
                        if startAltitude is None:
                            startAltitude = alt
                        trajectory.append([lon * 1e-7, lat * 1e-7, alt - startAltitude, t])
                        timeTrajectory[t] = [lon * 1e-7, lat * 1e-7, alt, t]
                if trajectory:
                    trajectory_data["AHRS3"] = {
                        "startAltitude": startAltitude,
                        "trajectory": trajectory,
                        "timeTrajectory": timeTrajectory
                    }

            # Human-readable vehicle type mapping
            vehicle_type_map = {
                1: 'airplane', 2: 'quadcopter', 3: 'quadcopter', 4: 'quadcopter', 5: 'tracker',
                10: 'rover', 11: 'boat', 12: 'submarine', 13: 'quadcopter', 14: 'quadcopter',
                15: 'quadcopter', 19: 'airplane', 20: 'airplane', 21: 'quadcopter', 22: 'airplane',
                23: 'airplane', 24: 'airplane', 29: 'quadcopter'
            }
            vehicle_type_human = None
            if vehicle_type is not None:
                vehicle_type_human = vehicle_type_map.get(vehicle_type, str(vehicle_type))

            # Output
            logger.info(f"Parsed {self.metadata['message_count']} messages")
            logger.info(f"Found message types: {', '.join(sorted(self.message_types))}")
            logger.info(f"Flight duration: {self.metadata.get('duration', 0):.2f} seconds")

            return {
                "metadata": self.metadata,
                "message_types": sorted(list(self.message_types)),
                "messages": self.messages,
                "attitude": attitude,
                "flight_modes": flight_modes,
                "events": events,
                "mission": mission,
                "params": params,
                "text_messages": text_messages,
                "named_value_float_names": named_value_float_names,
                "start_time": start_time,
                "vehicle_type": vehicle_type,
                "vehicle_type_human": vehicle_type_human,
                "attitude_sources": attitude_sources,
                "trajectory_sources": trajectory_sources,
                "trajectory_data": trajectory_data
            }
        except Exception as e:
            logger.error(f"Error parsing file {self.file_path}: {e}")
            raise
            
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