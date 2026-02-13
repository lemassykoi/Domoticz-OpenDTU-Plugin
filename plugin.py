# OpenDTU Data Collector via WebSocket
#
# Author: lemassykoi
#
"""
<plugin key="OpenDTU" name="OpenDTU Data Collector" author="lemassykoi" version="0.8.0" externallink="https://github.com/lemassykoi/Domoticz-OpenDTU-Plugin">
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
        <param field="Mode6" label="Debug" width="150px">
            <options>
                <option label="None" value="0" default="true"/>
                <option label="Plugin Debug" value="2"/>
                <option label="All" value="1"/>
            </options>
        </param>
    </params>
</plugin>
"""

import Domoticz  # type: ignore
import json
import secrets
import base64
import urllib.parse
import urllib.request

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


class RoomPlanManager:
    def __init__(self):
        self.conn = None
        self.plan_name = ""
        self.state = "IDLE"
        self.plan_idx = None
        self.plan_device_set = set()
        self.pending_add = []

    def start(self, plan_name, port, created_device_idxs):
        self.plan_name = plan_name
        self.pending_add = [str(x) for x in created_device_idxs if x is not None]
        if not self.pending_add or not self.plan_name or not port:
            return
        self.conn = Domoticz.Connection(
            Name="DomoticzPlanHTTP", Transport="TCP/IP", Protocol="HTTP",
            Address="127.0.0.1", Port=str(port)
        )
        self.state = "GET_PLANS"
        self.conn.Connect()

    def on_connect(self, status, description):
        if status != 0:
            Domoticz.Error(f"PlanHTTP connect failed: {description}")
            self.state = "ERROR"
            return
        self._send_next()

    def on_message(self, data):
        if not isinstance(data, dict) or "Status" not in data:
            return
        if data["Status"] != "200":
            Domoticz.Error(f"PlanHTTP API returned HTTP {data['Status']}")
            self.state = "ERROR"
            return
        raw = data.get("Data", b"")
        try:
            obj = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except Exception as e:
            Domoticz.Error(f"PlanHTTP invalid JSON: {e}")
            self.state = "ERROR"
            return
        self._handle_response(obj)
        self._send_next()

    def _send_api(self, params):
        qs = urllib.parse.urlencode(params)
        self.conn.Send({"Verb": "GET", "URL": f"/json.htm?{qs}",
                        "Headers": {"Host": "127.0.0.1", "Accept": "application/json",
                                    "Connection": "keep-alive"}})

    def _send_next(self):
        if self.state in ("IDLE", "DONE", "ERROR"):
            return
        if self.state == "GET_PLANS" or self.state == "GET_PLANS_AFTER_CREATE":
            self._send_api({"type": "command", "param": "getplans", "order": "name", "used": "true"})
        elif self.state == "ADD_PLAN":
            self._send_api({"type": "command", "param": "addplan", "name": self.plan_name})
        elif self.state == "GET_PLAN_DEVICES":
            self._send_api({"type": "command", "param": "getplandevices", "idx": int(self.plan_idx)})
        elif self.state == "ADD_DEVICE_NEXT":
            self._add_next_device()

    def _add_next_device(self):
        while self.pending_add:
            dev_idx = self.pending_add.pop(0)
            if dev_idx in self.plan_device_set:
                Domoticz.Debug(f"Device IDX {dev_idx} already in plan - skipping")
                continue
            Domoticz.Log(f"Adding device IDX {dev_idx} to plan IDX {self.plan_idx}...")
            self._send_api({"type": "command", "param": "addplanactivedevice",
                            "activeidx": int(dev_idx), "activetype": 0, "idx": int(self.plan_idx)})
            return
        self.state = "DONE"
        Domoticz.Log(f"Room plan '{self.plan_name}' sync complete.")

    def _handle_response(self, obj):
        if obj.get("status") != "OK" and self.state != "GET_PLAN_DEVICES":
            Domoticz.Error(f"PlanHTTP API error in state {self.state}: {obj}")
            self.state = "ERROR"
            return
        if self.state in ("GET_PLANS", "GET_PLANS_AFTER_CREATE"):
            found = None
            for p in obj.get("result", []) or []:
                if p.get("Name") == self.plan_name:
                    found = p.get("idx")
                    break
            if found:
                Domoticz.Log(f"Found room plan '{self.plan_name}' with IDX: {found}")
                self.plan_idx = found
                self.state = "GET_PLAN_DEVICES"
            elif self.state == "GET_PLANS":
                Domoticz.Log(f"Room plan '{self.plan_name}' not found. Creating it...")
                self.state = "ADD_PLAN"
            else:
                Domoticz.Error(f"Created plan '{self.plan_name}' but failed to find its IDX.")
                self.state = "ERROR"
        elif self.state == "ADD_PLAN":
            Domoticz.Log(f"Room plan '{self.plan_name}' created. Re-fetching IDX...")
            self.state = "GET_PLANS_AFTER_CREATE"
        elif self.state == "GET_PLAN_DEVICES":
            self.plan_device_set = set()
            for d in obj.get("result", []) or []:
                devidx = d.get("devidx")
                if devidx is not None:
                    self.plan_device_set.add(str(devidx))
            self.state = "ADD_DEVICE_NEXT"
        elif self.state == "ADD_DEVICE_NEXT":
            pass


class NotificationManager:
    def __init__(self):
        self.port = None

    def configure(self, port):
        self.port = port

    def send(self, message, notifier):
        if not self.port or not notifier:
            Domoticz.Debug(f"Notification suppressed (notifier not configured): '{message}'")
            return
        Domoticz.Log(f"Sending notification: '{message}' via '{notifier}'")
        qs = urllib.parse.urlencode({
            "type": "command",
            "param": "sendnotification",
            "subject": "OpenDTU Alert",
            "body": message,
            "subsystem": notifier.lower()
        })
        url = f"http://127.0.0.1:{self.port}/json.htm?{qs}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                Domoticz.Debug(f"Notification sent (HTTP {resp.status})")
        except Exception as e:
            Domoticz.Error(f"Notification failed: {e}")


class BasePlugin:
    def __init__(self):
        self.websocketConn = None
        self.discoveryConn = None
        self.planMgr = RoomPlanManager()
        self.notifMgr = NotificationManager()
        self.inverter_states = {}
        self.notif_all_started = False
        self.notif_all_stopped = True
        self.daily_report_sent = True
        self.reconAgain = 3
        self.last_global_svalue = ""
        self.last_total_yield = 0
        self.yield_offset = 0
        self.room_plan_name = "Solar"
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

        self.notifMgr.configure(_domoticz_port)
        self.room_plan_name = Parameters.get("Mode4", "Solar").strip() or "Solar"

        if 1 in Devices and Devices[1].sValue:
            try:
                parts = Devices[1].sValue.split(";")
                if len(parts) >= 2:
                    self.last_total_yield = int(float(parts[1]))
                    Domoticz.Log(f"Restored last total yield from device: {self.last_total_yield} Wh")
            except (ValueError, IndexError):
                pass

        self.discoveryConn = Domoticz.Connection(
            Name="OpenDTUDiscovery", Transport="TCP/IP", Protocol="HTTP",
            Address=Parameters["Address"], Port="80"
        )
        self.discoveryConn.Connect()

    def _handleDiscoveryResponse(self, data):
        status_code = int(data.get("Status", "0"))
        if status_code != 200:
            Domoticz.Error(f"OpenDTU discovery failed with HTTP {status_code}")
            self.connectWebSocket()
            return

        try:
            raw = data.get("Data", b"")
            inverters_data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except Exception as e:
            Domoticz.Error(f"Failed to parse inverter list: {e}")
            self.connectWebSocket()
            return

        created_devices = []
        unit_id_counter = 3
        for inverter in inverters_data.get('inverter', []):
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

        if _domoticz_port and created_devices:
            created_device_idxs = [Devices[u].ID for u in created_devices if u in Devices]
            self.planMgr.start(self.room_plan_name, _domoticz_port, created_device_idxs)

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
        if Connection.Name == "DomoticzPlanHTTP":
            self.planMgr.on_connect(Status, Description)
            return
        if Connection.Name == "OpenDTUDiscovery":
            if Status == 0:
                auth_string = f"{Parameters['Username']}:{Parameters['Password']}"
                auth_b64 = base64.b64encode(auth_string.encode()).decode()
                Connection.Send({
                    "Verb": "GET", "URL": "/api/inverter/list",
                    "Headers": {
                        "Host": Parameters["Address"],
                        "Authorization": f"Basic {auth_b64}",
                        "Accept": "application/json",
                        "Connection": "close"
                    }
                })
            else:
                Domoticz.Error(f"Failed to connect to OpenDTU for discovery: {Description}")
                self.connectWebSocket()
            return
        if Connection.Name == "OpenDTUWebSocket":
            if Status == 0:
                Domoticz.Log(f"Connected to OpenDTU at {Connection.Address}:{Connection.Port}")
                Connection.Send({
                    'URL': '/livedata',
                    'Headers': {
                        'Host': Parameters["Address"],
                        'Origin': 'http://' + Parameters["Address"],
                        'Sec-WebSocket-Key': base64.b64encode(secrets.token_bytes(16)).decode("utf-8")
                    }
                })
            else:
                Domoticz.Error(f"Failed to connect ({Status}) to OpenDTU: {Description}")

    def onMessage(self, Connection, Data):
        if Connection.Name == "DomoticzPlanHTTP":
            self.planMgr.on_message(Data)
            return
        if Connection.Name == "OpenDTUDiscovery":
            self._handleDiscoveryResponse(Data)
            return
        if Connection.Name == "OpenDTUWebSocket":
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
                payload = json.loads(Data["Payload"])
                self.processLiveData(payload)
            except Exception as e:
                Domoticz.Error(f"Error processing WebSocket message: {e}")

    def processLiveData(self, live_data):
        if 'inverters' not in live_data or len(live_data['inverters']) == 0:
            return

        inverter_data = live_data['inverters'][0]
        serial = inverter_data['serial']

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

        current_producing = inverter_data.get('producing', False)
        was_producing = self.inverter_states[serial]['producing']

        if current_producing and not was_producing:
            if Parameters["Mode3"] != "0":
                self.notifMgr.send(f"☀️ Solar production started for {inverter_name}", Parameters["Mode2"])
            self.inverter_states[serial]['producing'] = True
        elif not current_producing and was_producing:
            if Parameters["Mode3"] != "0":
                self.notifMgr.send(f"🌙 Solar production stopped for {inverter_name}", Parameters["Mode2"])
            self.inverter_states[serial]['producing'] = False

        all_producing = all(state['producing'] for state in self.inverter_states.values())
        none_producing = not any(state['producing'] for state in self.inverter_states.values())

        if all_producing and not self.notif_all_started:
            self.notifMgr.send("🔆 All inverters are now producing!", Parameters["Mode2"])
            if 2 in Devices:
                Devices[2].Update(nValue=1, sValue="On")
            self.notif_all_started = True
            self.notif_all_stopped = False
            self.daily_report_sent = False

        elif none_producing and not self.notif_all_stopped:
            if 2 in Devices:
                Devices[2].Update(nValue=0, sValue="Off")

            msg = "🌜 All inverters have stopped production."
            if not self.daily_report_sent and 'total' in live_data:
                daily_yield = float(live_data['total']['YieldDay']['v'])
                energy_in_kwh = daily_yield / 1000
                msg += f"\n🌞 Production Solaire du Jour : {energy_in_kwh:.3f} kWh"
                self.daily_report_sent = True
            self.notifMgr.send(msg, Parameters["Mode2"])
            self.notif_all_stopped = True
            self.notif_all_started = False

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
        if Connection.Name == "OpenDTUDiscovery":
            return
        if Connection.Name == "DomoticzPlanHTTP":
            return
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


# Generic helper functions
def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            if x == "Password":
                Domoticz.Debug(f"'{x}':'***HIDDEN***'")
            else:
                Domoticz.Debug(f"'{x}':'{Parameters[x]}'")
    Domoticz.Debug(f"Device count: {len(Devices)}")
    for x in Devices:
        Domoticz.Debug(f"Device: {x} - {Devices[x]}")
        Domoticz.Debug(f"Device ID:       '{Devices[x].ID}'")
        Domoticz.Debug(f"Device Name:     '{Devices[x].Name}'")
        Domoticz.Debug(f"Device nValue:    {Devices[x].nValue}")
        Domoticz.Debug(f"Device sValue:   '{Devices[x].sValue}'")
        Domoticz.Debug(f"Device LastLevel: {Devices[x].LastLevel}")

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
