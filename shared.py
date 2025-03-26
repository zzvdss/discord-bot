import discord
from discord.ext import commands
import os
import json
import time
import logging
from collections import defaultdict
from typing import Dict, List, Any, Optional

# Constants
LOG_CHANNEL_IDS = [1351561404150448248, 1350543441821564988]
PRISON_DURATION = 3600  # 1 hour in seconds
PRISON_ROLE_NAME = "Prisoner"
ADMIN_USER_IDS = [776744923738800129]
MOD_ROLE_NAME = "Moderator"
REPORT_COOLDOWN = 3600  # 1 hour cooldown between reports
REQUIRED_VOTES = 3
VOTE_DURATION = 300  # 5 minutes
VOTE_COOLDOWN = 900  # 15 minutes
REPORT_NOTICE_THRESHOLD = 2  # Notice at 2 reports
REPORT_DM_THRESHOLD = 3      # DM at 3 reports
REPORT_PRISON_THRESHOLD = 15 # Prison at 15 reports


# File paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DATA_FILE = os.path.join(SCRIPT_DIR, "report_data.json")
PRISON_DATA_FILE = os.path.join(SCRIPT_DIR, "prison_data.json")

# Data storage
reported_users = defaultdict(lambda: {
    'count': 0,
    'reasons': [],
    'last_report': 0
})
user_roles_before_prison = {}
user_nicknames_before_prison = {}
vote_sessions = {}
vote_cooldowns = {}
dm_permissions = defaultdict(list)
imprisonment_times = {}

# Initialize logger
logger = logging.getLogger("discord_bot")

def ensure_directory_exists():
    """Ensure the data directory exists"""
    os.makedirs(SCRIPT_DIR, exist_ok=True)

def save_data(data, file_path: str, default_factory=None):
    """Save data to file with thread safety"""
    ensure_directory_exists()
    try:
        with open(file_path, 'w') as f:
            if default_factory:
                json.dump(dict(data), f, indent=4)
            else:
                json.dump(data, f, indent=4)
        return True
    except Exception as e:
        logger.error(f"Error saving to {file_path}: {e}")
        return False

def load_data(file_path: str, default_factory=None):
    """Load data from file"""
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                data = json.load(f)
                if default_factory:
                    return defaultdict(default_factory, data)
                return data
        if default_factory:
            return defaultdict(default_factory)
        return {}
    except Exception as e:
        logger.error(f"Error loading from {file_path}: {e}")
        if default_factory:
            return defaultdict(default_factory)
        return {}

def is_mod_or_admin(ctx: commands.Context) -> bool:
    """Check if user is mod or admin"""
    if ctx.author.id in ADMIN_USER_IDS:
        return True
    mod_role = discord.utils.get(ctx.guild.roles, name=MOD_ROLE_NAME)
    if mod_role and mod_role in ctx.author.roles:
        return True
    return ctx.author.guild_permissions.administrator

async def log_activity(bot: commands.Bot, message: str) -> None:
    """Log activity to designated channels"""
    for channel_id in LOG_CHANNEL_IDS:
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                await channel.send(message)
            except discord.errors.HTTPException as e:
                logger.error(f"Error sending log message to channel {channel_id}: {e}")

def load_initial_data():
    """Load initial data from files"""
    global reported_users, user_roles_before_prison, user_nicknames_before_prison
    
    # Load report data
    report_data = load_data(REPORT_DATA_FILE, lambda: {
        'count': 0,
        'reasons': [],
        'last_report': 0
    })
    reported_users.update(report_data)
    
    # Load prison data
    prison_data = load_data(PRISON_DATA_FILE)
    if prison_data:
        user_roles_before_prison.update({
            int(user_id): prison_data['user_roles'].get(user_id, [])
            for user_id in prison_data.get('user_roles', {})
        })
        user_nicknames_before_prison.update(prison_data.get('user_nicknames', {}))

# Load data when module is imported
load_initial_data()