#!/bin/bash
# Install script for Lancache Prefill Dashboard
# Usage: curl ... | bash

# Variables
REPO_URL="https://github.com/Retrotrigger/lancache-dashboard.git"
INSTALL_DIR="/opt/lancache-tools/prefill"
LOG_DIR="$INSTALL_DIR/logs"
CONFIG_FILE="$INSTALL_DIR/config.json"
PREFILL_DIR="$INSTALL_DIR/prefill-tools"

# GitHub repositories for prefill tools
STEAM_PREFILL_REPO="https://github.com/tpill90/steam-lancache-prefill.git"
EPIC_PREFILL_REPO="https://github.com/tpill90/epic-lancache-prefill.git"
BATTLENET_PREFILL_REPO="https://github.com/Furdarius/battlenet-lancache-prefill.git"

# Check for root privileges
if [ "$(id -u)" -ne 0 ]; then
  echo "This script must be run as root" >&2
  exit 1
fi

# Clone repo if not exists
if [ ! -d "$INSTALL_DIR" ]; then
  echo "Installing Lancache Prefill Dashboard..."
  mkdir -p "$INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
else
  echo "Updating Lancache Prefill Dashboard..."
  git -C "$INSTALL_DIR" pull
fi

# Create logs directory
mkdir -p "$LOG_DIR"
chmod 755 "$LOG_DIR"

# Create directory for prefill tools
mkdir -p "$PREFILL_DIR"

# Install Python dependencies
echo "Installing dependencies..."
apt update && apt install -y python3 python3-pip git dotnet-sdk-6.0
pip3 install -r $INSTALL_DIR/requirements.txt

# Download and install prefill scripts if they don't exist
echo "Checking for prefill tools..."

# Function to install a prefill tool from GitHub
install_prefill_tool() {
  local repo_url=$1
  local tool_dir=$2
  local tool_name=$3
  
  if [ ! -d "$tool_dir" ]; then
    echo "Installing $tool_name from $repo_url..."
    git clone "$repo_url" "$tool_dir"
    
    # Handle any tool-specific installation steps
    case "$tool_name" in
      "Steam Prefill")
        # Build the Steam prefill tool using dotnet
        if [ -f "$tool_dir/SteamPrefill.sln" ]; then
          echo "Building Steam Prefill tool..."
          (cd "$tool_dir" && dotnet build -c Release)
          # Create symlink to the built tool
          ln -sf "$tool_dir/bin/Release/net6.0/SteamPrefill" "$INSTALL_DIR/steamprefill"
        fi
        ;;
      "Epic Prefill")
        # Build the Epic prefill tool using dotnet
        if [ -f "$tool_dir/EpicPrefill.sln" ]; then
          echo "Building Epic Prefill tool..."
          (cd "$tool_dir" && dotnet build -c Release)
          # Create symlink to the built tool
          ln -sf "$tool_dir/bin/Release/net6.0/EpicPrefill" "$INSTALL_DIR/epicprefill"
        fi
        ;;
      "BattleNet Prefill")
        # Compile BattleNet prefill
        if [ -f "$tool_dir/Makefile" ]; then
          echo "Building BattleNet Prefill tool..."
          (cd "$tool_dir" && make)
          # Create symlink to the built tool
          ln -sf "$tool_dir/battlenetprefill" "$INSTALL_DIR/battlenetprefill"
        fi
        ;;
    esac
    
    echo "$tool_name installed successfully"
  else
    echo "$tool_name already exists, updating..."
    (cd "$tool_dir" && git pull)
  fi
}

# Install each prefill tool
install_prefill_tool "$STEAM_PREFILL_REPO" "$PREFILL_DIR/steam-prefill" "Steam Prefill"
install_prefill_tool "$EPIC_PREFILL_REPO" "$PREFILL_DIR/epic-prefill" "Epic Prefill"
install_prefill_tool "$BATTLENET_PREFILL_REPO" "$PREFILL_DIR/battlenet-prefill" "BattleNet Prefill"

# Remove any existing cron jobs for prefill tools
echo "Checking for existing cron jobs..."
CRONTAB_FILE=$(mktemp)

# Export current crontab
crontab -l > "$CRONTAB_FILE" 2>/dev/null || echo "" > "$CRONTAB_FILE"

# Remove lines containing prefill tools
echo "Removing existing prefill cron jobs..."
grep -v "steamprefill\|epicprefill\|battlenetprefill" "$CRONTAB_FILE" > "${CRONTAB_FILE}.new"
mv "${CRONTAB_FILE}.new" "$CRONTAB_FILE"

# Import updated crontab
crontab "$CRONTAB_FILE"
rm "$CRONTAB_FILE"

echo "Prefill cron jobs have been removed. Scheduling will now be managed by the dashboard."

# Create default config if it doesn't exist
if [ ! -f "$CONFIG_FILE" ]; then
  echo "Creating default configuration..."
  
  # Detect actual paths for the prefill tools
  STEAM_PATH="$INSTALL_DIR/steamprefill"
  EPIC_PATH="$INSTALL_DIR/epicprefill"
  BATTLENET_PATH="$INSTALL_DIR/battlenetprefill"
  
  # Fallback to basic names if not found
  [ ! -f "$STEAM_PATH" ] && [ ! -L "$STEAM_PATH" ] && STEAM_PATH="steamprefill"
  [ ! -f "$EPIC_PATH" ] && [ ! -L "$EPIC_PATH" ] && EPIC_PATH="epicprefill"
  [ ! -f "$BATTLENET_PATH" ] && [ ! -L "$BATTLENET_PATH" ] && BATTLENET_PATH="battlenetprefill"
  
  cat <<EOF >$CONFIG_FILE
{
    "password": "admin",
    "games": {
        "steam": [],
        "epic": [],
        "battlenet": []
    },
    "schedules": [],
    "prefill_tools": {
        "steam": "$STEAM_PATH",
        "epic": "$EPIC_PATH",
        "battlenet": "$BATTLENET_PATH"
    }
}
EOF
  chmod 600 "$CONFIG_FILE"
  echo "Default configuration created. Default password is 'admin'. Please change it after login."
fi

# Setup systemd service
echo "Setting up systemd service..."
SERVICE_FILE=/etc/systemd/system/lancache-dashboard.service
cat <<EOF >$SERVICE_FILE
[Unit]
Description=Lancache Prefill Dashboard
After=network.target

[Service]
ExecStart=/usr/bin/python3 $INSTALL_DIR/app.py
WorkingDirectory=$INSTALL_DIR
Restart=always
Environment=FLASK_ENV=production
User=root

[Install]
WantedBy=multi-user.target
EOF

# Restart service
systemctl daemon-reload
systemctl enable lancache-dashboard
systemctl restart lancache-dashboard

# Create a MOTD file for the dashboard
MOTD_FILE="/etc/motd.d/10-lancache-dashboard"

# Create directory if it doesn't exist
mkdir -p /etc/motd.d

# Create the MOTD content
cat <<EOF >$MOTD_FILE

╔════════════════════════════════════════════════════════════════╗
║                 LANCACHE PREFILL DASHBOARD                     ║
╚════════════════════════════════════════════════════════════════╝

  Dashboard URL: http://$(hostname -I | awk '{print $1}'):8080
  Default credentials: admin (change this in Settings)
  Config location: $CONFIG_FILE
  Installation directory: $INSTALL_DIR

For help or issues: https://github.com/Retrotrigger/lancache-dashboard

EOF

# Make MOTD file executable if using update-motd system
chmod +x $MOTD_FILE

# For systems without motd.d support, add a link in the main motd file
if [ -f "/etc/motd" ]; then
  grep -q "LANCACHE PREFILL DASHBOARD" /etc/motd || {
    echo "\nLancache Dashboard URL: http://$(hostname -I | awk '{print $1}'):8080\n" >> /etc/motd
  }
fi

echo "Lancache Prefill Dashboard installed successfully!"
echo "Access the dashboard at http://$(hostname -I | awk '{print $1}'):8080"
echo "Default password: admin"
echo "Remember to change the default password in the Settings page."
