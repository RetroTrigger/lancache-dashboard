# Lancache Prefill Dashboard

A local web interface to manage SteamPrefill, EpicPrefill, and BattleNetPrefill tools.

## Features
- Select games to prefill from your game library
- Integrated game selection interface
- Schedule prefill tasks
- View logs and status
- Detect existing cache and skip
- Dark and light mode support with customizable appearance

## Installation & Usage

### Prerequisites
1. Ensure you have Python 3.6+ and pip installed
2. Install the prefill tools for the platforms you want to support (Steam, Epic, Battle.net)

### Installation
1. Clone this repository or download the latest release
2. Run `install.sh` to install dependencies and set up the dashboard
3. The installer will prompt you to create a password for the dashboard

### Running the Dashboard
1. The dashboard will start automatically after installation
2. **The service is configured to autostart on system boot** (installed as a systemd service)
3. To start it manually if needed: `sudo systemctl start lancache-dashboard`
4. Visit http://<server-ip>:8080 in your browser
5. Log in with the password you created during installation

### Using the Dashboard
1. **Game Selection**: Browse and select games from your library to prefill
2. **Scheduling**: Set up automatic prefill schedules
3. **Monitoring**: View logs and prefill status
4. **Settings**: Configure dashboard and prefill options

### Managing the Service
- **Restart the service**: `sudo systemctl restart lancache-dashboard`
- **Check service status**: `sudo systemctl status lancache-dashboard`
- **Disable autostart**: `sudo systemctl disable lancache-dashboard`
- **Enable autostart**: `sudo systemctl enable lancache-dashboard`
- **View logs**: `sudo journalctl -u lancache-dashboard`

## Requirements
- Python 3.6+
- Flask
- Prefill tools installed in /opt/lancache-tools/prefill/ (or configured path)
- Web browser with JavaScript enabled

## Customization
The dashboard supports both light and dark modes:
- Dark mode: Optimized for low-light environments
- Light mode: Uses a neutral gray color scheme to reduce eye strain

You can customize the appearance by modifying the CSS variables in `static/style.css`.

## Recent Changes
- Fixed CSS syntax issues for better browser compatibility
- Updated light mode with a neutral gray color scheme for reduced eye strain
- Integrated game selection interface directly into the dashboard
- Improved synchronization between dashboard selections and prefill tools
- Removed redundant manual game entry in favor of the improved selection interface

## Development
Contributions are welcome! Please feel free to submit a Pull Request.
