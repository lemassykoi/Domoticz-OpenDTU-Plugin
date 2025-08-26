# OpenDTU Data Collector without MQTT
#
# Author: lemassykoi
#
"""
<plugin key="OpenDTU" name="OpenDTU Data Collector" author="lemassykoi" version="0.5.0" wikilink="https://github.com/lemassykoi/Domoticz-OpenDTU-Plugin" externallink="https://github.com/openDTU/openDTU">
    <description>
        <h2>OpenDTU Data Collector</h2><br/>
        Collects solar data from an OpenDTU unit without relying on MQTT. The plugin automatically discovers inverters and creates corresponding devices in Domoticz.
        <h3>Features</h3>
        <ul style="list-style-type:square">
            <li>Automatically discovers inverters and creates devices on startup.</li>
            <li>Collects total power, daily yield, and total yield from OpenDTU.</li>
            <li>Collects per-inverter power and daily yield.</li>
            <li>Sends notifications when production starts and stops (configurable).</li>
            <li>Sends a daily summary report at the end of the production day.</li>
        </ul>
        <h3>Devices</h3>
        <ul style="list-style-type:square">
            <li><b>Global Solar Production</b> (General, kWh): Shows current total power and total lifetime yield.</li>
            <li><b>Inverter [Name]</b> (General, kWh): One device per discovered inverter, showing its current power and daily yield.</li>
        </ul>
        <h3>Configuration</h3>
        <ul style="list-style-type:square">
            <li><b>OpenDTU IP Address</b>: The IP address of your OpenDTU unit.</li>
            <li><b>Username/Password</b>: Credentials to access the OpenDTU web interface. Needed to get inverters list.</li>
            <li><b>Polling Interval</b>: How often (in seconds) to fetch data from the OpenDTU. A value of 10 seconds is recommended.</li>
            <li><b>Notifier</b>: Select the Domoticz Notifier (Telegram or None) to use for alerts. You must configure this in Domoticz's Notifications Settings first.</li>
            <li><b>Send Individual Notifications</b>: Enable/disable notifications for individual inverter start/stop events.</li>
            <li><b>Room Plan Name</b>: Name of the room plan where all solar devices will be organized. Default is "Solar".</li>
        </ul>
    </description>
    <params>
        <param field="Address" label="OpenDTU IP Address" width="200px" required="true" default="192.168.1.100"/>
        <param field="Username" label="OpenDTU Username" width="200px" required="true" default="admin"/>
        <param field="Password" label="OpenDTU Password" width="200px" required="true" password="true"/>
        <param field="Mode1" label="Polling Interval (sec)" width="75px" required="true" default="10"/>
        <param field="Mode2" label="Notifier" width="200px">
            <options>
                <option label="None" value="" default="true" />
                <option label="Telegram" value="Telegram"/>
            </options>
        </param>
        <param field="Mode3" label="Send Individual Notifications" width="75px">
            <options>
                <option label="True" value="1"/>
                <option label="False" value="0" default="false" />
            </options>
        </param>
        <param field="Mode4" label="Room Plan Name" width="200px" required="false" default="Solar"/>
        <param field="Mode6" label="Debug" width="75px">
            <options>
                <option label="True" value="1"/>
                <option label="False" value="0" default="true" />
                <option label="Plugin Only" value="2"/>
            </options>
        </param>
    </params>
</plugin>
"""

import Domoticz
import requests
import threading
import queue
import urllib.parse
import time

# Device IDs for easy reference
GLOBAL_DEVICE_ID = "OpenDTU_Global"
GLOBAL_DEVICE_NAME = "Solar Production Counter"
PROD_SWITCH_NAME = "Production Solaire"

class BasePlugin:
    def __init__(self):
        # State variables to hold data between heartbeats
        self.poll_timer = 0
        self.inverter_states = {}  # Will store { 'serial': {'producing': False, 'name': 'inverter_name', 'unit': unit_id} }
        self.notif_all_started = False
        self.notif_all_stopped = True
        self.daily_report_sent = True
        # Threading for async HTTP requests
        self.messageQueue = queue.Queue()
        self.messageThread = None
        return

    def onStart(self):
        Domoticz.Log("onStart called")

        if Parameters["Mode6"] != "0":
            Domoticz.Debugging(int(Parameters["Mode6"]))
            DumpConfigToLog()

        # Set up polling interval from parameters (in heartbeats, heartbeat = 10 sec)
        try:
            poll_seconds = int(Parameters["Mode1"])
            # Convert seconds to heartbeats (heartbeat = 10 seconds)
            self.poll_interval = max(1, poll_seconds // 10)  # At least 1 heartbeat (10 sec)
            Domoticz.Log(f"Polling every {self.poll_interval} heartbeats ({self.poll_interval * 10} seconds)")
        except Exception:
            Domoticz.Error("Invalid polling interval. Defaulting to 1 heartbeat (10 seconds).")
            self.poll_interval = 1

        # Construct URLs for OpenDTU API calls
        self.dtu_base_url = f"http://{Parameters['Address']}"
        self.dtu_auth_url = f"http://{Parameters['Username']}:{Parameters['Password']}@{Parameters['Address']}"
        
        # Start message handling thread
        self.messageThread = threading.Thread(name="OpenDTUThread", target=self.handleMessage)
        self.messageThread.start()

        # --- Device Discovery and Creation ---
        try:
            # Get or create room plan with user-specified name
            room_plan_name = Parameters.get("Mode4", "Solar").strip() or "Solar"
            solar_plan_idx = get_room_plan_idx(room_plan_name)
            
            # Fetch the list of inverters from OpenDTU (requires auth)
            inverters_list_response = requests.get(f'{self.dtu_auth_url}/api/inverter/list', timeout=5)
            inverters_list_response.raise_for_status()
            inverters_data = inverters_list_response.json()

            created_devices = []  # Track newly created devices to add to room plan
            
            unit_id_counter = 3 # Start inverter units from 3
            for inverter in inverters_data['inverter']:
                name = inverter['name']
                serial = inverter['serial']
                Domoticz.Log(f"Discovered Inverter: {name} (Serial: {serial})")

                # Initialize state for this inverter
                self.inverter_states[serial] = {'producing': False, 'name': name, 'unit': unit_id_counter}

                # Create a Domoticz device for this inverter if it doesn't exist
                if unit_id_counter not in Devices:
                    Domoticz.Log(f"Creating device for inverter '{name}' with Unit={unit_id_counter}, DeviceID='{serial}'")
                    Domoticz.Device(Name=name, Unit=unit_id_counter, TypeName="kWh", Subtype=29, Switchtype=4, DeviceID=str(serial), Used=1, Options={'EnergyMeterMode': '1' }).Create()
                    created_devices.append(unit_id_counter)
                    Domoticz.Debug(f"Device created with keys: {list(Devices.keys())}")
                else:
                    Domoticz.Debug(f"Device for inverter '{name}' already exists")
                unit_id_counter += 1

            # Create a global device for total production if it doesn't exist
            if 1 not in Devices:
                Domoticz.Log(f"Creating global device with Unit=1, DeviceID='{GLOBAL_DEVICE_ID}'")
                Domoticz.Device(Name=GLOBAL_DEVICE_NAME, Unit=1, TypeName="kWh", Subtype=29, Switchtype=4, DeviceID=GLOBAL_DEVICE_ID, Used=1, Options={'EnergyMeterMode': '1' }).Create()
                created_devices.append(1)
                Domoticz.Debug(f"Global device created, available devices: {list(Devices.keys())}")
            else:
                Domoticz.Debug(f"Found existing device: Unit 1 - {Devices[1].Name}")

            # Create switch for production state
            if 2 not in Devices:
                Domoticz.Log(f"Creating global device with Unit=2, DeviceID='{PROD_SWITCH_NAME}'")
                Domoticz.Device(Name=PROD_SWITCH_NAME, Unit=2, TypeName="On/Off", Switchtype=0, Image=32, DeviceID="OpenDTU_Prod_Switch", Used=1).Create()
                created_devices.append(2)
            else:
                Domoticz.Debug(f"Found existing device: Unit 2 - {Devices[2].Name}")
                
            # Add newly created devices to Solar room plan
            if solar_plan_idx and created_devices:
                time.sleep(2)  # Give Domoticz time to create devices
                for unit_id in created_devices:
                    if unit_id in Devices:
                        device_idx = Devices[unit_id].ID
                        add_device_to_plan(device_idx, solar_plan_idx)

        except requests.exceptions.RequestException as e:
            Domoticz.Error(f"Could not connect to OpenDTU at '{Parameters['Address']}'. Check IP, credentials, and network. Error: {e}")
        except Exception as e:
            Domoticz.Error(f"An unexpected error occurred during onStart: {e}")

        # Set the poll timer to fire on the first heartbeat
        self.poll_timer = self.poll_interval
        Domoticz.Debug(f"Initial poll_timer set to {self.poll_timer}, poll_interval={self.poll_interval}")

    def handleMessage(self):
        """Handle async HTTP requests in separate thread"""
        try:
            Domoticz.Debug("Entering message handler")
            while True:
                Domoticz.Debug("Waiting for message in queue...")
                Message = self.messageQueue.get(block=True)
                Domoticz.Debug(f"Received message: {Message}")
                if Message is None:
                    Domoticz.Debug("Exiting message handler")
                    self.messageQueue.task_done()
                    break

                if Message["Type"] == "Poll":
                    Domoticz.Debug("Processing Poll message")
                    self.poll_dtu()
                else:
                    Domoticz.Debug(f"Unknown message type: {Message['Type']}")
                    
                self.messageQueue.task_done()
                Domoticz.Debug("Message processed, waiting for next...")
        except Exception as err:
            Domoticz.Error(f"handleMessage: {err}")

    def onHeartbeat(self):
        Domoticz.Debug(f"onHeartbeat called, poll_timer={self.poll_timer}, poll_interval={self.poll_interval}")
        self.poll_timer -= 1
        Domoticz.Debug(f"poll_timer decremented to {self.poll_timer}")
        if self.poll_timer <= 0:
            self.poll_timer = self.poll_interval  # Reset timer
            Domoticz.Debug("Queuing OpenDTU data poll...")
            # Queue async polling request
            self.messageQueue.put({"Type": "Poll"})
        else:
            Domoticz.Debug(f"Not polling yet, {self.poll_timer} heartbeats remaining")

    def poll_dtu(self):
        """Perform the actual DTU polling in background thread"""
        try:
            Domoticz.Debug("Polling OpenDTU for data...")
            
            # Make a single API call to get all live data
            live_data_response = requests.get(f'{self.dtu_base_url}/api/livedata/status', timeout=5)
            live_data_response.raise_for_status()
            live_data = live_data_response.json()
            
            Domoticz.Debug(f"Received live data with {len(live_data['inverters'])} inverters")
            Domoticz.Debug(f"Available devices: {list(Devices.keys())}")

            # --- Update Global Solar Device ---
            total_power = float(live_data['total']['Power']['v'])
            total_yield_lifetime = int(float(live_data['total']['YieldTotal']['v']) * 1000) # Convert kWh to Wh
            daily_yield = float(live_data['total']['YieldDay']['v']) # In Wh
            
            Domoticz.Debug(f"Global values: Power={total_power}W, Total={total_yield_lifetime}Wh, Daily={daily_yield}Wh")

            if 1 in Devices:
                sValue = f"{total_power};{total_yield_lifetime}"
                Domoticz.Debug(f"Updating global device (Unit 1) with sValue: {sValue}")
                Devices[1].Update(nValue=0, sValue=sValue)
                Domoticz.Debug("Global device updated successfully")
            else:
                Domoticz.Error("Global device (Unit 1) not found in Devices!")
            
            # --- Update Individual Inverter Devices and Handle Notifications ---
            for inverter_data in live_data['inverters']:
                serial = inverter_data['serial']
                Domoticz.Debug(f"Processing inverter serial: {serial}")
                
                if serial not in self.inverter_states:
                    Domoticz.Error(f"Serial {serial} not found in inverter_states! Available states: {list(self.inverter_states.keys())}")
                    continue

                unit_id = self.inverter_states[serial]['unit']
                
                if unit_id not in Devices:
                    Domoticz.Error(f"Unit {unit_id} for serial {serial} not found in Devices! Available devices: {list(Devices.keys())}")
                    continue

                current_producing_state = inverter_data['producing']
                was_producing = self.inverter_states[serial]['producing']
                inverter_name = self.inverter_states[serial]['name']

                # Get detailed data for this specific inverter
                try:
                    inv_response = requests.get(f'{self.dtu_base_url}/api/livedata/status?inv={serial}', timeout=5)
                    inv_response.raise_for_status()
                    inv_data = inv_response.json()
                    
                    if 'inverters' in inv_data and len(inv_data['inverters']) > 0:
                        detailed_inv = inv_data['inverters'][0]
                        # Update device values from detailed inverter data
                        power = float(detailed_inv['AC']['0']['Power']['v'])
                        yield_day = int(detailed_inv['DC']['0']['YieldDay']['v'])
                        sValue = f"{power};{yield_day}"
                        
                        Domoticz.Debug(f"Inverter {inverter_name} ({serial}): Power={power}W, YieldDay={yield_day}Wh, Producing={current_producing_state}")
                        Domoticz.Debug(f"Updating device Unit {unit_id} with sValue: {sValue}")
                        
                        Devices[unit_id].Update(nValue=0, sValue=sValue)
                        Domoticz.Debug(f"Device Unit {unit_id} updated successfully")
                    else:
                        Domoticz.Error(f"No detailed data available for inverter {serial}")
                except requests.exceptions.RequestException as e:
                    Domoticz.Error(f"Failed to get detailed data for inverter {serial}: {e}")
                except Exception as e:
                    Domoticz.Error(f"Error processing detailed data for inverter {serial}: {e}")

                # Check for start/stop production events
                try:
                    if current_producing_state and not was_producing:
                        if Parameters["Mode3"] != "0":
                            self.send_notification(f"☀️ Solar production started for {inverter_name}")
                        self.inverter_states[serial]['producing'] = True
                    elif not current_producing_state and was_producing:
                        if Parameters["Mode3"] != "0":
                            self.send_notification(f"🌙 Solar production stopped for {inverter_name}")
                        self.inverter_states[serial]['producing'] = False
                except Exception as e:
                    Domoticz.Error(f"Error handling notifications for {inverter_name}: {e}")
                    # Update the state anyway
                    self.inverter_states[serial]['producing'] = current_producing_state

            # --- Check Overall Production State for Group Notifications ---
            all_producing = all(state['producing'] for state in self.inverter_states.values())
            none_producing = not any(state['producing'] for state in self.inverter_states.values())

            if all_producing and not self.notif_all_started:
                self.send_notification("🔆 All inverters are now producing!")
                Devices[2].Update(nValue=1, sValue="On")
                self.notif_all_started = True
                self.notif_all_stopped = False
                self.daily_report_sent = False # Reset for the day

            elif none_producing and not self.notif_all_stopped:
                self.send_notification("🌜 All inverters have stopped production.")
                Devices[2].Update(nValue=0, sValue="Off")
                self.notif_all_stopped = True
                self.notif_all_started = False
                
                # --- Send Daily Report ---
                if not self.daily_report_sent:
                    energy_in_kwh = daily_yield / 1000
                    message = f"🌞 Production Solaire du Jour : {energy_in_kwh:.3f} kWh"
                    self.send_notification(message)
                    self.daily_report_sent = True

        except requests.exceptions.RequestException as e:
            Domoticz.Error(f"Failed to poll OpenDTU. Error: {e}")
        except Exception as e:
            Domoticz.Error(f"An error occurred during data processing: {e}")

    def send_notification(self, message):
        notifier = Parameters["Mode2"]
        if notifier:
            Domoticz.Log(f"Sending notification: '{message}' via '{notifier}'")
            # Use HTTP request to Domoticz JSON API for notifications
            try:
                subject = urllib.parse.quote("OpenDTU Alert")
                body = urllib.parse.quote(message)
                subsystem = notifier.lower()  # Convert to lowercase (Telegram -> telegram)
                
                notification_url = f"http://127.0.0.1/json.htm?type=command&param=sendnotification&subject={subject}&body={body}&subsystem={subsystem}"
                
                response = requests.get(notification_url, timeout=5)
                response.raise_for_status()
                
                Domoticz.Debug(f"Notification sent successfully via {notifier}")
            except Exception as e:
                Domoticz.Error(f"Failed to send notification: {e}")
        else:
            Domoticz.Debug(f"Notification suppressed (notifier not configured): '{message}'")

    def onStop(self):
        Domoticz.Log("onStop called")
        
        # signal queue thread to exit
        self.messageQueue.put(None)
        Domoticz.Log("Clearing message queue...")
        self.messageQueue.join()

        # Wait until queue thread has exited
        import time
        Domoticz.Log("Threads still active: "+str(threading.active_count())+", should be 1.")
        while (threading.active_count() > 1):
            for thread in threading.enumerate():
                if (thread.name != threading.current_thread().name):
                    Domoticz.Log("'"+thread.name+"' is still running, waiting otherwise Domoticz will abort on plugin exit.")
            time.sleep(1.0)

    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Log(f"onCommand called for Unit {Unit}: Command '{Command}', Level: {Level}")

# --- Boilerplate code for Domoticz plugin framework ---

global _plugin
_plugin = BasePlugin()

def onStart():
    global _plugin
    _plugin.onStart()

def onStop():
    global _plugin
    _plugin.onStop()

def onCommand(Unit, Command, Level, Hue):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Hue)

def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()

# Room Plan Management Functions
def domoticz_api_call(params, is_utility_call=False):
    url = "http://127.0.0.1/json.htm"
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get("status") == "OK":
            if not is_utility_call: 
                action_title = params.get("param", "Unknown Action")
                name_param_value = params.get('sensorname', params.get('name', "Unknown Device"))

                if action_title == "addplanactivedevice":
                     Domoticz.Log(f"API call '{action_title}' successful for device IDX {params.get('activeidx')} to plan IDX {params.get('idx')}.")
                elif action_title == "addplan":
                    Domoticz.Log(f"API call '{action_title}' for plan '{params.get('name')}' successful. API Title: {data.get('title')}")
                elif action_title == "setused":
                    Domoticz.Log(f"API call '{action_title}' for device IDX {params.get('idx')} ('{name_param_value}') successful. API Title: {data.get('title')}")
                else: 
                    new_idx = data.get("idx")
                    Domoticz.Log(f"Device '{name_param_value}' action successful (IDX: {new_idx if new_idx else 'N/A'}). API Title: {data.get('title')}")
            return data 
        else: 
            Domoticz.Error(f"Domoticz API error for params {params}: {data.get('message', 'Unknown error')}. Full response: {data}")
            return None
    except requests.exceptions.RequestException as e: 
        Domoticz.Error(f"Request failed for params {params}: {e}")
        return None
    except Exception as e: 
        Domoticz.Error(f"Failed to decode JSON for params {params}: {e}. Response: {response.text if 'response' in locals() else 'N/A'}")
        return None

def find_plan_idx_in_response(plan_name, data):
    if data and data.get("status") == "OK" and "result" in data:
        for plan in data["result"]:
            if plan.get("Name") == plan_name:
                plan_idx = plan.get("idx")
                Domoticz.Log(f"Found room plan '{plan_name}' with IDX: {plan_idx}")
                return plan_idx
    return None

def get_room_plan_idx(plan_name):
    Domoticz.Log(f"Finding room plan IDX for '{plan_name}'...")
    params_getplans = {"type": "command", "param": "getplans", "order": "name", "used": "true"}
    data = domoticz_api_call(params_getplans, is_utility_call=True)
    found_idx = find_plan_idx_in_response(plan_name, data)
    if found_idx: 
        return found_idx
    else:
        Domoticz.Log(f"Room plan '{plan_name}' not found. Creating it...")
        params_addplan = {"type": "command", "param": "addplan", "name": plan_name}
        creation_data = domoticz_api_call(params_addplan, is_utility_call=False) 
        if creation_data and creation_data.get("status") == "OK":
            Domoticz.Log(f"Room plan '{plan_name}' created. Re-fetching IDX...")
            time.sleep(1) 
            data_after_create = domoticz_api_call(params_getplans, is_utility_call=True)
            newly_created_idx = find_plan_idx_in_response(plan_name, data_after_create)
            if newly_created_idx: 
                return newly_created_idx
            else: 
                Domoticz.Error(f"Created plan '{plan_name}' but failed to find its IDX.")
                return None
        else: 
            Domoticz.Error(f"Failed to create room plan '{plan_name}'.")
            return None

def add_device_to_plan(device_idx, plan_idx):
    Domoticz.Log(f"Adding device IDX {device_idx} to plan IDX {plan_idx}...")
    try: 
        dev_idx_int, plan_idx_int = int(device_idx), int(plan_idx)
    except ValueError: 
        Domoticz.Error(f"Invalid IDX for addplanactivedevice: dev='{device_idx}', plan='{plan_idx}'.")
        return
    params = {"type": "command", "param": "addplanactivedevice", "activeidx": dev_idx_int, "activetype": 0, "idx": plan_idx_int}
    result = domoticz_api_call(params, is_utility_call=False) 
    if not (result and result.get("status") == "OK"): 
        Domoticz.Error(f"Failed to add device IDX {device_idx} to plan IDX {plan_idx}.")

# Generic helper functions
def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            Domoticz.Debug(f"'{x}':'{str(Parameters[x])}'")
    Domoticz.Debug("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device:           " + str(x) + " - " + str(Devices[x]))
        Domoticz.Debug("Device ID:       '" + str(Devices[x].ID) + "'")
        Domoticz.Debug("Device Name:     '" + Devices[x].Name + "'")
        Domoticz.Debug("Device nValue:    " + str(Devices[x].nValue))
        Domoticz.Debug("Device sValue:   '" + Devices[x].sValue + "'")
        Domoticz.Debug("Device LastLevel: " + str(Devices[x].LastLevel))
