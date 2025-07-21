# Domoticz OpenDTU Plugin

A Domoticz plugin that collects solar production data from an OpenDTU unit without requiring MQTT. The plugin automatically discovers inverters and creates corresponding devices in Domoticz with smart notifications and room plan organization.

![Domoticz Logo](https://cdn.brandfetch.io/idT9Rk1_Fn/w/196/h/196/theme/light/logo.png?c=1bxid64Mup7aczewSAYMX&t=1748444255713) ![OpenDTU Logo](https://www.opendtu.solar/assets/images/logo.png)

## Features

- **🔍 Automatic Discovery**: Automatically discovers all connected inverters and creates devices on startup
- **📊 Comprehensive Monitoring**: Collects total power, daily yield, and total lifetime yield from OpenDTU
- **🔌 Per-Inverter Tracking**: Individual monitoring of each inverter's power and daily yield
- **📱 Smart Notifications**: Configurable notifications for production start/stop events (Telegram support)
- **📈 Daily Reports**: Automatic daily summary reports at end of production day
- **🏠 Room Plan Integration**: Automatically organizes all solar devices into a configurable room plan
- **⚡ Real-time Updates**: Live data polling with configurable intervals
- **🚫 No MQTT Required**: Direct HTTP API communication with OpenDTU

## Requirements

- **Domoticz** 2020.2 or later
- **OpenDTU** device accessible via HTTP
- **Python 3.9+** with requests library
- Network connectivity between Domoticz and OpenDTU

## Installation

1. **Clone or download** this repository to your Domoticz plugins directory:
   ```bash
   cd /path/to/domoticz/plugins
   git clone https://github.com/lemassykoi/Domoticz-OpenDTU-Plugin.git
   ```

2. **Restart Domoticz** to load the plugin

3. **Add Hardware** in Domoticz:
   - Go to Setup → Hardware
   - Type: OpenDTU Data Collector
   - Configure the required parameters (see Configuration section)

## Configuration

### Required Parameters

| Parameter | Description | Default | Example |
|-----------|-------------|---------|---------|
| **OpenDTU IP Address** | IP address of your OpenDTU device | `192.168.1.100` | `192.168.1.50` |
| **OpenDTU Username** | Username for OpenDTU web interface | `admin` | `admin` |
| **OpenDTU Password** | Password for OpenDTU web interface | - | `your_password` |
| **Polling Interval** | Data polling frequency in seconds | `10` | `30` |

### Optional Parameters

| Parameter | Description | Default | Options |
|-----------|-------------|---------|---------|
| **Notifier** | Domoticz notification system to use | None | None, Telegram |
| **Send Individual Notifications** | Enable notifications for individual inverters | False | True, False |
| **Room Plan Name** | Name of room plan for device organization | `Solar` | `Solar`, `PV System` |
| **Debug Level** | Logging verbosity | False | True, False, Plugin Only |

## Created Devices

The plugin automatically creates the following devices:

### Global Devices
- **Solar Production** (kWh): Shows current total power and total lifetime yield
- **Production Solaire** (Switch): Indicates overall production status (On/Off)

### Per-Inverter Devices
- **[Inverter Name]** (kWh): Individual inverter showing current power and daily yield

All devices are automatically organized into the specified room plan for better dashboard organization.

## Notifications

When configured with a notifier (Telegram), the plugin sends:

- **Individual Notifications** (if enabled):
  - ☀️ Solar production started for [Inverter Name]
  - 🌙 Solar production stopped for [Inverter Name]

- **Global Notifications**:
  - 🔆 All inverters are now producing!
  - 🌜 All inverters have stopped production.

- **Daily Reports**:
  - 🌞 Production Solaire du Jour : X.XXX kWh

## API Endpoints Used

The plugin communicates with these OpenDTU API endpoints:

- `/api/inverter/list` - Inverter discovery (requires authentication)
- `/api/livedata/status` - Live production data
- `/api/livedata/status?inv=[serial]` - Individual inverter details

## Troubleshooting

### Common Issues

**Plugin not starting:**
- Verify OpenDTU IP address is correct and accessible
- Check username/password credentials
- Ensure OpenDTU web interface is enabled

**No data updates:**
- Check network connectivity between Domoticz and OpenDTU
- Verify polling interval is reasonable (minimum 10 seconds recommended)
- Check Domoticz logs for error messages

**Notifications not working:**
- Configure Telegram (or other notifier) in Domoticz Notifications Settings first
- Ensure notifier is selected in plugin configuration
- Check Domoticz notification system is working independently

### Debug Mode

Enable debug logging by setting **Debug Level** to:
- **True**: Full debug output
- **Plugin Only**: Plugin-specific debug messages only

Debug logs will appear in the Domoticz log with detailed API communication and data processing information.

## OpenDTU Compatibility

This plugin is compatible with:
- OpenDTU v23.x and later
- All inverter types supported by OpenDTU (Hoymiles, TSUN, etc.)
- Single and multi-inverter installations

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [OpenDTU Project](https://github.com/tbnobody/OpenDTU) for the excellent solar inverter monitoring solution
- [Domoticz](https://www.domoticz.com/) for the home automation platform
- Solar community for testing and feedback

## Support

If you find this plugin useful, consider:
- ⭐ Starring this repository
- 🐛 Reporting issues on GitHub
- 💡 Contributing improvements or features

For support, please open an issue on GitHub with:
- Domoticz version
- OpenDTU version
- Plugin configuration
- Relevant log entries
