# OpenDTU Data Collector via WebSocket
#
# Author: lemassykoi
#
"""
<plugin key="OpenDTU" name="OpenDTU Data Collector" author="lemassykoi" version="0.7.0" externallink="https://github.com/lemassykoi/Domoticz-OpenDTU-Plugin">
    <description>
        <h2>OpenDTU Data Collector</h2><br/>
        Collects solar data from an OpenDTU unit via WebSocket. The plugin automatically discovers inverters and creates corresponding devices in Domoticz.
        <h3>Features</h3>
        <ul style="list-style-type:square">
            <li>Connects via WebSocket for near-realtime data updates.</li>
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
            <li><b>Notifier</b>: Select the Domoticz Notifier (Telegram or None) to use for alerts. You must configure this in Domoticz's Notifications Settings first.</li>
            <li><b>Send Individual Notifications</b>: Enable/disable notifications for individual inverter start/stop events.</li>
            <li><b>Room Plan Name</b>: Name of the room plan where all solar devices will be organized. Default is "Solar".</li>
        </ul>
    </description>
    <params>
        <param field="Address" label="OpenDTU IP Address" width="200px" required="true" default="192.168.1.100"/>
        <param field="Username" label="OpenDTU Username" width="200px" required="true" default="admin"/>
        <param field="Password" label="OpenDTU Password" width="200px" required="true" password="true"/>
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
import secrets
import base64
import urllib.parse
import time

GLOBAL_DEVICE_ID = "OpenDTU_Global"
GLOBAL_DEVICE_NAME = "Solar Counter"
PROD_SWITCH_NAME = "Solar Production"

_domoticz_port = None


def get_domoticz_http_port():
    with open("/proc/self/cmdline", "rb") as f:
        args = [a.decode() for a in f.read().split(b'\x00') if a]
    for i, arg in enumerate(args):
        if arg == "-www" and i + 1 < len(args):
            return int(args[i + 1])
    return None


class BasePlugin:
    def __init__(self):
        self.websocketConn = None
        self.inverter_states = {}
        self.notif_all_started = False
        self.notif_all_stopped = True
        self.daily_report_sent = True
        self.reconAgain = 3
        self.last_global_svalue = ""
        self.last_total_yield = 0
        self.yield_offset = 0
        return

    def onStart(self):
        global _domoticz_port
        Domoticz.Log("onStart called")

        if Parameters["Mode6"] != "0":
            Domoticz.Debugging(int(Parameters["Mode6"]))
            DumpConfigToLog()

        domoticz_http_port = get_domoticz_http_port()
        if domoticz_http_port is not None:
            Domoticz.Log(f"Domoticz detected HTTP Port: {domoticz_http_port}")
            _domoticz_port = domoticz_http_port
        else:
            Domoticz.Error("Failed to detect Domoticz HTTP Port")

        # --- Device Discovery and Creation (one-time HTTP call) ---
        dtu_auth_url = f"http://{Parameters['Username']}:{Parameters['Password']}@{Parameters['Address']}"
        try:
            room_plan_name = Parameters.get("Mode4", "Solar").strip() or "Solar"
            solar_plan_idx = get_room_plan_idx(room_plan_name) if _domoticz_port else None

            inverters_list_response = requests.get(f'{dtu_auth_url}/api/inverter/list', timeout=5)
            inverters_list_response.raise_for_status()
            inverters_data = inverters_list_response.json()

            created_devices = []
            unit_id_counter = 3
            for inverter in inverters_data['inverter']:
                name = inverter['name']
                serial = inverter['serial']
                Domoticz.Log(f"Discovered Inverter: {name} (Serial: {serial})")

                self.inverter_states[serial] = {'producing': False, 'name': name, 'unit': unit_id_counter}

                if unit_id_counter not in Devices:
                    Domoticz.Log(f"Creating device for inverter '{name}' with Unit={unit_id_counter}, DeviceID='{serial}'")
                    Domoticz.Device(Name=name, Unit=unit_id_counter, TypeName="kWh", Subtype=29, Switchtype=4, DeviceID=str(serial), Used=1, Options={'EnergyMeterMode': '1'}).Create()
                    created_devices.append(unit_id_counter)
                unit_id_counter += 1

            if 1 not in Devices:
                Domoticz.Log(f"Creating global device with Unit=1, DeviceID='{GLOBAL_DEVICE_ID}'")
                Domoticz.Device(Name=GLOBAL_DEVICE_NAME, Unit=1, TypeName="kWh", Subtype=29, Switchtype=4, DeviceID=GLOBAL_DEVICE_ID, Used=1, Options={'EnergyMeterMode': '1'}).Create()
                created_devices.append(1)

            if 2 not in Devices:
                Domoticz.Log(f"Creating production switch with Unit=2, DeviceID='OpenDTU_Prod_Switch'")
                Domoticz.Device(Name=PROD_SWITCH_NAME, Unit=2, TypeName="On/Off", Switchtype=0, Image=32, DeviceID="OpenDTU_Prod_Switch", Used=1).Create()
                created_devices.append(2)

            if solar_plan_idx and created_devices:
                time.sleep(2)
                for unit_id in created_devices:
                    if unit_id in Devices:
                        device_idx = Devices[unit_id].ID
                        add_device_to_plan(device_idx, solar_plan_idx)

        except requests.exceptions.RequestException as e:
            Domoticz.Error(f"Could not connect to OpenDTU at '{Parameters['Address']}'. Check IP, credentials, and network. Error: {e}")
        except Exception as e:
            Domoticz.Error(f"An unexpected error occurred during onStart: {e}")

        # --- Restore last known total yield from existing device ---
        if 1 in Devices and Devices[1].sValue:
            try:
                parts = Devices[1].sValue.split(";")
                if len(parts) >= 2:
                    self.last_total_yield = int(float(parts[1]))
                    Domoticz.Log(f"Restored last total yield from device: {self.last_total_yield} Wh")
            except (ValueError, IndexError):
                pass

        # --- Connect WebSocket ---
        self.connectWebSocket()

    def connectWebSocket(self):
        Domoticz.Log("Connecting WebSocket to OpenDTU...")
        self.websocketConn = Domoticz.Connection(
            Name="OpenDTUWebSocket",
            Transport="TCP/IP",
            Protocol="WS",
            Address=Parameters["Address"],
            Port="80"
        )
        self.websocketConn.Connect()

    def onConnect(self, Connection, Status, Description):
        if Status == 0:
            Domoticz.Log(f"Connected to OpenDTU at {Connection.Address}:{Connection.Port}")
            send_data = {
                'URL': '/livedata',
                'Headers': {
                    'Host': Parameters["Address"],
                    'Origin': 'http://' + Parameters["Address"],
                    'Sec-WebSocket-Key': base64.b64encode(secrets.token_bytes(16)).decode("utf-8")
                }
            }
            Connection.Send(send_data)
        else:
            Domoticz.Error(f"Failed to connect ({Status}) to OpenDTU: {Description}")

    def onMessage(self, Connection, Data):
        if "Status" in Data:
            if Data["Status"] == "101":
                Domoticz.Log("WebSocket connection established to /livedata")
            else:
                Domoticz.Error(f"WebSocket upgrade failed with status {Data['Status']}")
                DumpWSResponseToLog(Data)
            return

        if "Operation" in Data:
            if Data["Operation"] == "Ping":
                Domoticz.Debug("Ping received, sending Pong")
                Connection.Send({'Operation': 'Pong', 'Payload': 'Pong', 'Mask': secrets.randbits(32)})
            elif Data["Operation"] == "Close":
                Domoticz.Log("WebSocket Close received from OpenDTU")
            return

        if "Payload" not in Data:
            return

        try:
            import json
            payload = json.loads(Data["Payload"])
            self.processLiveData(payload)
        except Exception as e:
            Domoticz.Error(f"Error processing WebSocket message: {e}")

    def processLiveData(self, live_data):
        if 'inverters' not in live_data or len(live_data['inverters']) == 0:
            return

        inverter_data = live_data['inverters'][0]
        serial = inverter_data['serial']

        # --- Update Global Device (only if value changed) ---
        if 'total' in live_data and 1 in Devices:
            total_power = float(live_data['total']['Power']['v'])
            raw_yield = int(float(live_data['total']['YieldTotal']['v']) * 1000)

            if raw_yield == 0:
                Domoticz.Debug("Ignoring YieldTotal of 0 (inverter likely unreachable)")
                sValue = f"{total_power};{self.yield_offset + self.last_total_yield}"
            else:
                if raw_yield < self.last_total_yield:
                    self.yield_offset += self.last_total_yield
                    Domoticz.Log(f"OpenDTU yield counter reset detected (was {self.last_total_yield} Wh, now {raw_yield} Wh). Adjusting offset to {self.yield_offset} Wh.")
                self.last_total_yield = raw_yield
                sValue = f"{total_power};{self.yield_offset + raw_yield}"

            if sValue != self.last_global_svalue:
                Devices[1].Update(nValue=0, sValue=sValue)
                self.last_global_svalue = sValue
                Domoticz.Debug(f"Global device updated: {sValue}")

        # --- Update Individual Inverter Device ---
        if serial not in self.inverter_states:
            Domoticz.Debug(f"Received data for unknown inverter {serial}, ignoring")
            return

        unit_id = self.inverter_states[serial]['unit']
        inverter_name = self.inverter_states[serial]['name']

        if unit_id in Devices:
            try:
                power = float(inverter_data['AC']['0']['Power']['v'])
                yield_day = int(inverter_data['DC']['0']['YieldDay']['v'])
                sValue = f"{power};{yield_day}"
                Devices[unit_id].Update(nValue=0, sValue=sValue)
                Domoticz.Debug(f"Inverter {inverter_name}: Power={power}W, YieldDay={yield_day}Wh")
            except (KeyError, TypeError) as e:
                Domoticz.Error(f"Missing data fields for inverter {inverter_name}: {e}")

        # --- Production State Notifications ---
        current_producing = inverter_data.get('producing', False)
        was_producing = self.inverter_states[serial]['producing']

        if current_producing and not was_producing:
            if Parameters["Mode3"] != "0":
                self.send_notification(f"☀️ Solar production started for {inverter_name}")
            self.inverter_states[serial]['producing'] = True
        elif not current_producing and was_producing:
            if Parameters["Mode3"] != "0":
                self.send_notification(f"🌙 Solar production stopped for {inverter_name}")
            self.inverter_states[serial]['producing'] = False

        # --- Overall Production State ---
        all_producing = all(state['producing'] for state in self.inverter_states.values())
        none_producing = not any(state['producing'] for state in self.inverter_states.values())

        if all_producing and not self.notif_all_started:
            self.send_notification("🔆 All inverters are now producing!")
            if 2 in Devices:
                Devices[2].Update(nValue=1, sValue="On")
            self.notif_all_started = True
            self.notif_all_stopped = False
            self.daily_report_sent = False

        elif none_producing and not self.notif_all_stopped:
            self.send_notification("🌜 All inverters have stopped production.")
            if 2 in Devices:
                Devices[2].Update(nValue=0, sValue="Off")
            self.notif_all_stopped = True
            self.notif_all_started = False

            if not self.daily_report_sent and 'total' in live_data:
                daily_yield = float(live_data['total']['YieldDay']['v'])
                energy_in_kwh = daily_yield / 1000
                message = f"🌞 Production Solaire du Jour : {energy_in_kwh:.3f} kWh"
                self.send_notification(message)
                self.daily_report_sent = True

    def send_notification(self, message):
        notifier = Parameters["Mode2"]
        if notifier and _domoticz_port:
            Domoticz.Log(f"Sending notification: '{message}' via '{notifier}'")
            try:
                subject = urllib.parse.quote("OpenDTU Alert")
                body = urllib.parse.quote(message)
                subsystem = notifier.lower()
                notification_url = f"http://127.0.0.1:{_domoticz_port}/json.htm?type=command&param=sendnotification&subject={subject}&body={body}&subsystem={subsystem}"
                response = requests.get(notification_url, timeout=5)
                response.raise_for_status()
                Domoticz.Debug(f"Notification sent successfully via {notifier}")
            except Exception as e:
                Domoticz.Error(f"Failed to send notification: {e}")
        else:
            Domoticz.Debug(f"Notification suppressed (notifier not configured): '{message}'")

    def onHeartbeat(self):
        if self.websocketConn and self.websocketConn.Connected():
            self.websocketConn.Send({'Operation': 'Ping', 'Mask': secrets.randbits(32)})
            Domoticz.Debug("Ping sent to OpenDTU")
            self.reconAgain = 3
        else:
            self.reconAgain -= 1
            if self.reconAgain <= 0:
                Domoticz.Log("Reconnecting WebSocket to OpenDTU...")
                self.connectWebSocket()
                self.reconAgain = 3
            else:
                Domoticz.Log(f"WebSocket disconnected, retrying in {self.reconAgain} heartbeats")

    def onDisconnect(self, Connection):
        Domoticz.Log("WebSocket disconnected from OpenDTU")

    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Log(f"onCommand called for Unit {Unit}: Command '{Command}', Level: {Level}")

    def onStop(self):
        Domoticz.Log("onStop called")
        if self.websocketConn and self.websocketConn.Connected():
            self.websocketConn.Send({'Operation': 'Close', 'Mask': secrets.randbits(32)})
            self.websocketConn.Disconnect()


# --- Boilerplate code for Domoticz plugin framework ---

global _plugin
_plugin = BasePlugin()

def onStart():
    global _plugin
    _plugin.onStart()

def onStop():
    global _plugin
    _plugin.onStop()

def onConnect(Connection, Status, Description):
    global _plugin
    _plugin.onConnect(Connection, Status, Description)

def onMessage(Connection, Data):
    global _plugin
    _plugin.onMessage(Connection, Data)

def onDisconnect(Connection):
    global _plugin
    _plugin.onDisconnect(Connection)

def onCommand(Unit, Command, Level, Hue):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Hue)

def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()


# Room Plan Management Functions
def domoticz_api_call(params, is_utility_call=False):
    url = f"http://127.0.0.1:{_domoticz_port}/json.htm"
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
            if x == "Password":  # Don't log API token
                Domoticz.Debug("'" + x + "':'***HIDDEN***'")
            else:
                Domoticz.Debug(f"'{x}':'{str(Parameters[x])}'")

    Domoticz.Debug("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device:           " + str(x) + " - " + str(Devices[x]))
        Domoticz.Debug("Device ID:       '" + str(Devices[x].ID) + "'")
        Domoticz.Debug("Device Name:     '" + Devices[x].Name + "'")
        Domoticz.Debug("Device nValue:    " + str(Devices[x].nValue))
        Domoticz.Debug("Device sValue:   '" + Devices[x].sValue + "'")
        Domoticz.Debug("Device LastLevel: " + str(Devices[x].LastLevel))

def DumpWSResponseToLog(httpDict):
    if isinstance(httpDict, dict):
        Domoticz.Log("WebSocket Details (" + str(len(httpDict)) + "):")
        for x in httpDict:
            if isinstance(httpDict[x], dict):
                Domoticz.Log("--->'"+x+" ("+str(len(httpDict[x]))+"):")
                for y in httpDict[x]:
                    Domoticz.Log("------->'" + y + "':'" + str(httpDict[x][y]) + "'")
            else:
                Domoticz.Log("--->'" + x + "':'" + str(httpDict[x]) + "'")
