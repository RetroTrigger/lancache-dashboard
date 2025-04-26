from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash
import os, subprocess, json, time, datetime, logging
from functools import wraps

# Configure logging
logging.basicConfig(filename='dashboard.log', level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

app = Flask(__name__)
app.secret_key = 'change-this-secret'  # Should be changed in production

# Constants
BASE_DIR = '/opt/lancache-tools/prefill'
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')

# Default config
DEFAULT_CONFIG = {
    "password": "admin",  # Default password, should be changed
    "games": {
        "steam": [],
        "epic": [],
        "battlenet": []
    },
    "schedules": [],
    "prefill_tools": {
        "steam": "steamprefill",
        "epic": "epicprefill",
        "battlenet": "battlenetprefill"
    }
}

# Ensure directories exist
os.makedirs(LOG_DIR, exist_ok=True)

# Load or create config
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            logging.error(f"Invalid JSON in {CONFIG_FILE}")
            return DEFAULT_CONFIG
    else:
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# Routes
@app.route('/')
@login_required
def dashboard():
    config = load_config()
    # Count games by platform
    game_counts = {
        'steam': len(config['games']['steam']),
        'epic': len(config['games']['epic']),
        'battlenet': len(config['games']['battlenet'])
    }
    total_games = sum(game_counts.values())
    
    # Get active jobs if any
    active_jobs = get_active_jobs()
    
    # Get upcoming schedules
    upcoming = get_upcoming_schedules(config['schedules'])
    
    return render_template('dashboard.html', 
                          game_counts=game_counts, 
                          total_games=total_games,
                          active_jobs=active_jobs,
                          upcoming=upcoming)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        config = load_config()
        if request.form.get('password') == config['password']:
            session['logged_in'] = True
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        else:
            flash('Invalid password')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/games')
@login_required
def games():
    config = load_config()
    return render_template('games.html', games=config['games'])

@app.route('/games/<platform>', methods=['GET', 'POST'])
@login_required
def manage_games(platform):
    if platform not in ['steam', 'epic', 'battlenet']:
        return redirect(url_for('games'))
    
    config = load_config()
    
    # Sync with the prefill tool's game selections
    tool_games = get_tool_selected_games(platform)
    if tool_games is not None:
        # Update our dashboard's game list with the tool's selections
        sync_tool_games_to_dashboard(platform, tool_games, config)
    
    if request.method == 'POST':
        if 'refresh_games' in request.form:
            # Force a refresh from the tool's selections
            tool_games = get_tool_selected_games(platform)
            if tool_games is not None:
                sync_tool_games_to_dashboard(platform, tool_games, config)
                flash(f"Refreshed {platform} game selections from the prefill tool.")
            else:
                flash(f"No games found or couldn't read selections from the {platform} prefill tool.")
    
    return render_template('manage_games.html', platform=platform, games=config['games'][platform])


@app.route('/select_games/<platform>', methods=['GET', 'POST'])
@login_required
def select_games(platform):
    """Handle game selection entirely within the dashboard"""
    if platform not in ['steam', 'epic', 'battlenet']:
        return redirect(url_for('games'))
    
    config = load_config()
    
    # Check if authenticated
    if not is_authenticated(platform):
        flash(f"You need to authenticate with {platform.capitalize()} first")
        return redirect(url_for('authenticate', platform=platform))
    
    # Get user's game library
    library = get_user_game_library(platform)
    
    # Get currently selected games (for checking selected status)
    selected_games = {g['id']: g for g in config['games'][platform]}
    
    if request.method == 'POST':
        if 'save_selection' in request.form:
            # Get selected game IDs from form
            selected_ids = request.form.getlist('game_ids')
            
            # Create a list of selected games
            selected = []
            for game in library:
                if game['id'] in selected_ids:
                    # Use existing data if game was already selected
                    if game['id'] in selected_games:
                        selected.append(selected_games[game['id']])
                    else:
                        selected.append({
                            'id': game['id'],
                            'name': game['name'],
                            'added': datetime.datetime.now().isoformat(),
                            'last_prefilled': None
                        })
            
            # Update config
            config['games'][platform] = selected
            save_config(config)
            
            # Write selections to prefill tool
            update_tool_selections(platform, selected_ids)
            
            flash(f"Game selection saved successfully")
            return redirect(url_for('manage_games', platform=platform))
    
    return render_template('select_games.html', 
                         platform=platform, 
                         library=library,
                         selected_games=selected_games)


def get_tool_selected_games(platform):
    """Read the selected games from the prefill tool's configuration"""
    config = load_config()
    tool_path = config['prefill_tools'][platform]
    
    # Check if tool exists
    if not (os.path.exists(tool_path) or os.path.islink(tool_path)):
        logging.error(f"Prefill tool not found at: {tool_path}")
        return None
    
    try:
        # Each tool stores its selections in a different way
        # Let's use their CLI interfaces to get the data
        if platform == 'steam':
            # Run SteamPrefill status command to get selected games
            cmd = f"{tool_path} select-apps status --json"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0 and result.stdout:
                try:
                    # Parse the JSON output
                    data = json.loads(result.stdout)
                    games = []
                    
                    # Handle the specific format of SteamPrefill output
                    if 'apps' in data:
                        for app in data['apps']:
                            games.append({
                                'id': str(app.get('appId', '')),
                                'name': app.get('name', f"App {app.get('appId', 'Unknown')}"),
                            })
                    return games
                except json.JSONDecodeError:
                    logging.error(f"Error parsing SteamPrefill JSON output")
            
        elif platform == 'epic':
            # Run EpicPrefill status command to get selected games
            cmd = f"{tool_path} select-apps status --json"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0 and result.stdout:
                try:
                    # Parse the JSON output
                    data = json.loads(result.stdout)
                    games = []
                    
                    # Handle the specific format of EpicPrefill output
                    if 'apps' in data:
                        for app in data['apps']:
                            games.append({
                                'id': app.get('catalogItemId', ''),
                                'name': app.get('displayName', f"App {app.get('catalogItemId', 'Unknown')}"),
                            })
                    return games
                except json.JSONDecodeError:
                    logging.error(f"Error parsing EpicPrefill JSON output")
            
        elif platform == 'battlenet':
            # Run BattleNetPrefill status command to get selected games
            cmd = f"{tool_path} select-apps status --json"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0 and result.stdout:
                try:
                    # Parse the JSON output
                    data = json.loads(result.stdout)
                    games = []
                    
                    # Handle the specific format of BattleNetPrefill output
                    if 'games' in data:
                        for game_id, game_info in data['games'].items():
                            games.append({
                                'id': game_id,
                                'name': game_info.get('name', f"Game {game_id}"),
                            })
                    return games
                except json.JSONDecodeError:
                    logging.error(f"Error parsing BattleNetPrefill JSON output")
    
    except Exception as e:
        logging.error(f"Error getting selected games from {platform} tool: {str(e)}")
    
    return None


def sync_tool_games_to_dashboard(platform, tool_games, config):
    """Sync the games selected in the prefill tool to the dashboard config"""
    if not tool_games:
        return False
    
    # Get current timestamp
    now = datetime.datetime.now().isoformat()
    
    # Keep track of existing games (for last_prefilled dates)
    existing_games = {g['id']: g.get('last_prefilled') for g in config['games'][platform]}
    
    # Create new list with games from tool
    new_games = []
    for game in tool_games:
        game_entry = {
            'id': game['id'],
            'name': game['name'],
            'added': now,
            'last_prefilled': existing_games.get(game['id'])
        }
        new_games.append(game_entry)
    
    # Update config and save
    config['games'][platform] = new_games
    save_config(config)
    
    return True


def get_user_game_library(platform):
    """Get the user's game library for the specified platform"""
    config = load_config()
    tool_path = config['prefill_tools'][platform]
    
    # Check if tool exists
    if not (os.path.exists(tool_path) or os.path.islink(tool_path)):
        logging.error(f"Prefill tool not found at: {tool_path}")
        return []
    
    try:
        # Use the library-list command of the prefill tool (if available)
        # Each platform's tool has different command structures
        if platform == 'steam':
            cmd = f"{tool_path} library-list --json"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0 and result.stdout:
                try:
                    # Parse the JSON output
                    data = json.loads(result.stdout)
                    games = []
                    
                    # Structure depends on the tool's output format
                    if 'apps' in data:
                        for app in data['apps']:
                            games.append({
                                'id': str(app.get('appId', '')),
                                'name': app.get('name', f"App {app.get('appId', 'Unknown')}"),
                                'size': app.get('sizeOnDisk', 0) if 'sizeOnDisk' in app else 0
                            })
                    return sorted(games, key=lambda x: x['name'])
                except json.JSONDecodeError:
                    logging.error(f"Error parsing Steam JSON output")
            
        elif platform == 'epic':
            cmd = f"{tool_path} library-list --json"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0 and result.stdout:
                try:
                    # Parse the JSON output
                    data = json.loads(result.stdout)
                    games = []
                    
                    # Structure depends on the tool's output format
                    if 'games' in data:
                        for game in data['games']:
                            games.append({
                                'id': game.get('catalogItemId', ''),
                                'name': game.get('displayName', f"Game {game.get('catalogItemId', 'Unknown')}"),
                                'size': game.get('sizeInBytes', 0) if 'sizeInBytes' in game else 0
                            })
                    return sorted(games, key=lambda x: x['name'])
                except json.JSONDecodeError:
                    logging.error(f"Error parsing Epic JSON output")
            
        elif platform == 'battlenet':
            cmd = f"{tool_path} library-list --json"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0 and result.stdout:
                try:
                    # Parse the JSON output
                    data = json.loads(result.stdout)
                    games = []
                    
                    # Structure depends on the tool's output format
                    if 'games' in data:
                        for game_id, game_info in data['games'].items():
                            games.append({
                                'id': game_id,
                                'name': game_info.get('name', f"Game {game_id}"),
                                'size': game_info.get('size', 0) if 'size' in game_info else 0
                            })
                    return sorted(games, key=lambda x: x['name'])
                except json.JSONDecodeError:
                    logging.error(f"Error parsing BattleNet JSON output")
        
        # Fallback: If we can't get the library directly, create a dummy list
        # This should be replaced with proper library fetching when available
        logging.warning(f"Could not get game library for {platform}, using fallback method")
        
        # As a fallback, use select-apps list with a timeout
        cmd = f"{tool_path} select-apps list --json"
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout:
                try:
                    data = json.loads(result.stdout)
                    return sorted([{'id': g.get('id', ''), 'name': g.get('name', '')} for g in data.get('games', [])], 
                                key=lambda x: x['name'])
                except json.JSONDecodeError:
                    pass
        except subprocess.TimeoutExpired:
            logging.error(f"Timeout while getting game library for {platform}")
    
    except Exception as e:
        logging.error(f"Error getting game library for {platform}: {str(e)}")
    
    return []


def update_tool_selections(platform, selected_ids):
    """Update the prefill tool's selections with the selected game IDs"""
    config = load_config()
    tool_path = config['prefill_tools'][platform]
    
    # Check if tool exists
    if not (os.path.exists(tool_path) or os.path.islink(tool_path)):
        logging.error(f"Prefill tool not found at: {tool_path}")
        return False
    
    try:
        # Create a temporary file with the selection list
        selection_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f'temp_{platform}_selection.json')
        
        # Format depends on the tool's expected input
        selection_data = {
            'apps': selected_ids if platform == 'steam' else [],
            'games': selected_ids if platform in ['epic', 'battlenet'] else []
        }
        
        with open(selection_file, 'w') as f:
            json.dump(selection_data, f)
        
        # Update the tool's selections
        cmd = f"{tool_path} select-apps update --file {selection_file}"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        # Clean up the temporary file
        if os.path.exists(selection_file):
            os.remove(selection_file)
        
        if result.returncode == 0:
            return True
        else:
            logging.error(f"Error updating {platform} selections: {result.stderr}")
            return False
            
    except Exception as e:
        logging.error(f"Error updating {platform} selections: {str(e)}")
        return False


def update_prefill_app_list(platform, config):
    """Update the prefill tool's app list based on dashboard configuration"""
    tool_path = config['prefill_tools'][platform]
    
    # Check if tool exists
    if not (os.path.exists(tool_path) or os.path.islink(tool_path)):
        logging.error(f"Prefill tool not found at: {tool_path}")
        return False
    
    # Different tools store their app lists in different locations
    # We'll need to implement platform-specific handling here
    # This is just a stub - actual implementation would depend on each tool's specifics
    
    # For now, log that we would update the list
    games = config['games'][platform]
    game_ids = [g['id'] for g in games]
    logging.info(f"Would update {platform} prefill list with: {', '.join(game_ids)}")
    
    # In the future, we could potentially generate the app lists for each tool
    # or use their API to update the selections
    
    return True


def is_authenticated(platform):
    """Check if the user is authenticated with the platform's prefill tool"""
    # This function checks if the user is already authenticated with the platform's prefill tool
    # Each tool maintains its own authentication state, typically in a token file
    
    # The exact check would depend on the platform and tool
    # For now, we'll do a simple check by looking for authentication files
    
    home_dir = os.path.expanduser("~")
    
    if platform == 'steam':
        # SteamPrefill stores auth tokens in a .steamprefill directory
        steam_auth_path = os.path.join(home_dir, '.steamprefill', 'cookies.json')
        return os.path.exists(steam_auth_path)
    
    elif platform == 'epic':
        # EpicPrefill stores auth tokens in a .epicprefill directory
        epic_auth_path = os.path.join(home_dir, '.epicprefill', 'auth.json')
        return os.path.exists(epic_auth_path)
    
    elif platform == 'battlenet':
        # BattleNetPrefill stores auth tokens in a .battlenetprefill directory
        bnet_auth_path = os.path.join(home_dir, '.battlenetprefill', 'auth.json')
        return os.path.exists(bnet_auth_path)
    
    return False


@app.route('/authenticate/<platform>', methods=['GET', 'POST'])
@login_required
def authenticate(platform):
    """Handle authentication for the platform's prefill tool"""
    if platform not in ['steam', 'epic', 'battlenet']:
        flash(f"Invalid platform: {platform}")
        return redirect(url_for('games'))
    
    config = load_config()
    auth_step = request.args.get('step', 'login')
    
    # Check if already authenticated
    if is_authenticated(platform) and auth_step == 'login':
        flash(f"You are already authenticated with {platform.capitalize()}")
        return redirect(url_for('manage_games', platform=platform))
    
    if request.method == 'POST':
        # Process authentication
        tool_path = config['prefill_tools'][platform]
        
        # Check if the tool exists
        if not (os.path.exists(tool_path) or os.path.islink(tool_path)):
            flash(f"Prefill tool not found at: {tool_path}")
            return redirect(url_for('manage_games', platform=platform))
        
        # Get credentials from form
        if platform == 'steam':
            username = request.form.get('username')
            password = request.form.get('password')
            mfa_code = request.form.get('mfa_code')
            
            if auth_step == 'login':
                # First authentication step
                log_file = os.path.join(LOG_DIR, f"auth_{platform}_{int(time.time())}.log")
                
                try:
                    # For Steam, we need to handle MFA separately
                    # Write credentials to a temp file for non-interactive login
                    cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp_auth.txt')
                    with open(cred_file, 'w') as f:
                        f.write(f"{username}\n{password}\n")
                    
                    # Use SteamPrefill with credentials file
                    cmd = f"{tool_path} login --credentials-file {cred_file}"
                    
                    with open(log_file, 'w') as logf:
                        process = subprocess.Popen(
                            cmd, 
                            shell=True, 
                            stdout=logf, 
                            stderr=subprocess.STDOUT
                        )
                        process.wait(timeout=30)
                    
                    # Securely delete the credentials file
                    if os.path.exists(cred_file):
                        os.remove(cred_file)
                    
                    # Check if login was successful or if MFA is needed
                    with open(log_file, 'r') as logf:
                        log_content = logf.read()
                    
                    if "Two-factor authentication" in log_content or "Steam Guard code" in log_content:
                        # Need MFA code
                        return render_template('authenticate.html', 
                                             platform=platform, 
                                             auth_step='mfa')
                    
                    if "Login Successful" in log_content or is_authenticated(platform):
                        flash(f"Successfully authenticated with {platform.capitalize()}")
                        return redirect(url_for('manage_games', platform=platform))
                    else:
                        flash(f"Authentication failed. Please check your credentials and try again.")
                
                except Exception as e:
                    flash(f"Error during authentication: {str(e)}")
                    logging.error(f"Authentication error: {str(e)}")
            
            elif auth_step == 'mfa' and mfa_code:
                # MFA step
                log_file = os.path.join(LOG_DIR, f"auth_mfa_{platform}_{int(time.time())}.log")
                
                try:
                    # Write MFA code to temp file
                    mfa_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp_mfa.txt')
                    with open(mfa_file, 'w') as f:
                        f.write(f"{mfa_code}\n")
                    
                    # Use SteamPrefill with MFA code
                    cmd = f"{tool_path} login --mfa-code-file {mfa_file}"
                    
                    with open(log_file, 'w') as logf:
                        process = subprocess.Popen(
                            cmd, 
                            shell=True, 
                            stdout=logf, 
                            stderr=subprocess.STDOUT
                        )
                        process.wait(timeout=30)
                    
                    # Securely delete the MFA file
                    if os.path.exists(mfa_file):
                        os.remove(mfa_file)
                    
                    # Check if login was successful
                    if is_authenticated(platform):
                        flash(f"Successfully authenticated with {platform.capitalize()}")
                        return redirect(url_for('manage_games', platform=platform))
                    else:
                        flash(f"Authentication failed. Please check your MFA code and try again.")
                
                except Exception as e:
                    flash(f"Error during MFA verification: {str(e)}")
                    logging.error(f"MFA verification error: {str(e)}")
        
        elif platform in ['epic', 'battlenet']:
            email = request.form.get('email')
            password = request.form.get('password')
            
            # Epic and Battle.net have similar authentication processes
            log_file = os.path.join(LOG_DIR, f"auth_{platform}_{int(time.time())}.log")
            
            try:
                # Write credentials to a temp file for non-interactive login
                cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f'temp_{platform}_auth.txt')
                with open(cred_file, 'w') as f:
                    f.write(f"{email}\n{password}\n")
                
                # Use prefill tool with credentials file
                cmd = f"{tool_path} login --credentials-file {cred_file}"
                
                with open(log_file, 'w') as logf:
                    process = subprocess.Popen(
                        cmd, 
                        shell=True, 
                        stdout=logf, 
                        stderr=subprocess.STDOUT
                    )
                    process.wait(timeout=30)
                
                # Securely delete the credentials file
                if os.path.exists(cred_file):
                    os.remove(cred_file)
                
                # Check if login was successful
                if is_authenticated(platform):
                    flash(f"Successfully authenticated with {platform.capitalize()}")
                    return redirect(url_for('manage_games', platform=platform))
                else:
                    flash(f"Authentication failed. Please check your credentials and try again.")
            
            except Exception as e:
                flash(f"Error during authentication: {str(e)}")
                logging.error(f"Authentication error: {str(e)}")
    
    # GET request or failed POST
    return render_template('authenticate.html', platform=platform, auth_step=auth_step)

@app.route('/schedule', methods=['GET', 'POST'])
@login_required
def schedule():
    config = load_config()
    
    if request.method == 'POST':
        if 'add_schedule' in request.form:
            platform = request.form.get('platform')
            day = request.form.get('day')
            time = request.form.get('time')
            
            if platform and day and time:
                schedule_id = str(int(time.time()))
                config['schedules'].append({
                    'id': schedule_id,
                    'platform': platform,
                    'day': day,
                    'time': time,
                    'enabled': True
                })
                save_config(config)
                flash("Schedule added successfully")
        
        elif 'toggle_schedule' in request.form:
            schedule_id = request.form.get('schedule_id')
            for sched in config['schedules']:
                if sched['id'] == schedule_id:
                    sched['enabled'] = not sched['enabled']
                    save_config(config)
                    break
        
        elif 'delete_schedule' in request.form:
            schedule_id = request.form.get('schedule_id')
            config['schedules'] = [s for s in config['schedules'] if s['id'] != schedule_id]
            save_config(config)
            flash("Schedule removed")
    
    return render_template('schedule.html', schedules=config['schedules'])

@app.route('/run/<platform>')
@login_required
def run_prefill(platform):
    if platform not in ['steam', 'epic', 'battlenet']:
        flash(f"Invalid platform: {platform}")
        return redirect(url_for('dashboard'))
    
    config = load_config()
    games = config['games'][platform]
    
    if not games:
        flash(f"No games configured for {platform}")
        return redirect(url_for('games'))
    
    # Start prefill job in background
    job_id = f"{platform}_{int(time.time())}"
    log_file = os.path.join(LOG_DIR, f"{job_id}.log")
    
    try:
        # Get prefill tool path
        tool_path = config['prefill_tools'][platform]
        
        # Build the appropriate command based on platform
        if platform == 'steam':
            # SteamPrefill uses the 'prefill' command
            command = f"{tool_path} prefill --non-interactive"
        elif platform == 'epic':
            # EpicPrefill uses the 'prefill' command
            command = f"{tool_path} prefill --non-interactive"
        elif platform == 'battlenet':
            # BattleNetPrefill uses the 'prefill' command
            command = f"{tool_path} prefill --non-interactive"
        
        # Add logging to help diagnose any issues
        logging.info(f"Running prefill command: {command}")
        
        # Run the command
        with open(log_file, 'w') as logf:
            process = subprocess.Popen(
                command, 
                shell=True, 
                stdout=logf, 
                stderr=subprocess.STDOUT,
                # Make sure the process can run in background
                start_new_session=True
            )
        
        flash(f"Started prefill job for {platform}. Check logs for progress.")
        
        # Update last_prefilled for all games in this platform
        now = datetime.datetime.now().isoformat()
        for game in config['games'][platform]:
            game['last_prefilled'] = now
        save_config(config)
    except Exception as e:
        flash(f"Error starting prefill: {str(e)}")
        logging.error(f"Error starting prefill: {str(e)}")
    
    return redirect(url_for('dashboard'))

@app.route('/logs')
@login_required
def view_logs():
    logs = []
    if os.path.exists(LOG_DIR):
        log_files = [f for f in os.listdir(LOG_DIR) if f.endswith('.log')]
        log_files.sort(reverse=True)  # Most recent first
        for log_file in log_files[:10]:  # Show 10 most recent logs
            log_path = os.path.join(LOG_DIR, log_file)
            platform = log_file.split('_')[0] if '_' in log_file else 'unknown'
            timestamp = os.path.getmtime(log_path)
            logs.append({
                'filename': log_file,
                'platform': platform,
                'time': datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S'),
                'size': os.path.getsize(log_path)
            })
    
    return render_template('logs.html', logs=logs)

@app.route('/logs/<filename>')
@login_required
def view_log_file(filename):
    log_path = os.path.join(LOG_DIR, filename)
    if not os.path.exists(log_path) or not filename.endswith('.log'):
        flash("Log file not found")
        return redirect(url_for('view_logs'))
    
    try:
        with open(log_path, 'r') as f:
            content = f.read()
        return render_template('log_detail.html', filename=filename, content=content)
    except Exception as e:
        flash(f"Error reading log: {str(e)}")
        return redirect(url_for('view_logs'))

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    config = load_config()
    message = None
    
    if request.method == 'POST':
        if 'update_password' in request.form:
            current = request.form.get('current_password')
            new = request.form.get('new_password')
            confirm = request.form.get('confirm_password')
            
            if current == config['password']:
                if new and new == confirm:
                    config['password'] = new
                    save_config(config)
                    message = "Password updated successfully"
                else:
                    message = "New passwords don't match"
            else:
                message = "Current password is incorrect"
        
        elif 'update_tool_path' in request.form:
            platform = request.form.get('platform')
            path = request.form.get('tool_path')
            
            if platform in config['prefill_tools'] and path:
                config['prefill_tools'][platform] = path
                save_config(config)
                message = f"Updated {platform} tool path"
    
    return render_template('settings.html', 
                          config=config,
                          message=message)

# Helper functions
def get_active_jobs():
    """Check for any running prefill jobs"""
    active = []
    try:
        # Simple check using process name, adapt to your environment
        output = subprocess.check_output(['ps', 'aux'], text=True)
        for tool in ['steamprefill', 'epicprefill', 'battlenetprefill']:
            if tool in output:
                platform = tool.replace('prefill', '')
                active.append({
                    'platform': platform,
                    'started': 'Unknown'  # Could parse from ps output if needed
                })
    except Exception as e:
        logging.error(f"Error checking active jobs: {str(e)}")
    return active

def get_upcoming_schedules(schedules):
    """Get schedules that are coming up soon"""
    upcoming = []
    now = datetime.datetime.now()
    for schedule in schedules:
        if not schedule['enabled']:
            continue
        
        # Simple schedule check - could be more sophisticated
        day_map = {'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3, 
                 'friday': 4, 'saturday': 5, 'sunday': 6}
        day_num = day_map.get(schedule['day'].lower(), -1)
        
        if day_num >= 0:
            days_ahead = (day_num - now.weekday()) % 7
            if days_ahead == 0 and now.strftime('%H:%M') > schedule['time']:
                days_ahead = 7  # Next week
                
            sched_time = datetime.datetime.strptime(schedule['time'], '%H:%M')
            next_run = now.replace(hour=sched_time.hour, minute=sched_time.minute, second=0) + \
                      datetime.timedelta(days=days_ahead)
            
            upcoming.append({
                'platform': schedule['platform'],
                'next_run': next_run.strftime('%Y-%m-%d %H:%M'),
                'id': schedule['id']
            })
    
    # Sort by next run time
    upcoming.sort(key=lambda x: x['next_run'])
    return upcoming[:5]  # Return 5 most imminent

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
