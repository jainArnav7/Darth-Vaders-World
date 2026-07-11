import os
import json
import random
import time
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ==========================================
# 1. STORAGE & CONFIGURATION FILE PATHS
# ==========================================
DEFAULT_PROMPTS_FILE = "default_prompts.json"
DATA_FILE = "custom_prompts.json"
STATS_FILE = "player_stats.json"

POINTS_MAP = {
    "normal": 1,
    "teen": 2,
    "18+": 3,
    "challenge": 5,
    "forfeit": -2
}

# Explicitly using utf-8 encoding to prevent Windows character map crashes
def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

# Load base configs from decoupled file
prompts_data = load_json(DEFAULT_PROMPTS_FILE)

DEFAULT_TRUTHS = prompts_data.get("truths", {})
DEFAULT_DARES = prompts_data.get("dares", {})
CHALLENGES = prompts_data.get("challenges", [])
PENALTIES = prompts_data.get("penalties", [])

# Dynamic server and player session stores
custom_storage = load_json(DATA_FILE)
stats_storage = load_json(STATS_FILE)
active_games = {}

def ensure_guild_storage(guild_id):
    if guild_id not in custom_storage:
        custom_storage[guild_id] = {"truth": [], "dare": {"in_person": [], "online": []}}

def ensure_user_stats(guild_id, user_id):
    guild_id, user_id = str(guild_id), str(user_id)
    if guild_id not in stats_storage:
        stats_storage[guild_id] = {}
    if user_id not in stats_storage[guild_id]:
        stats_storage[guild_id][user_id] = {
            "points": 0,
            "truths_completed": 0,
            "dares_completed": 0,
            "challenges_completed": 0,
            "forfeits": 0
        }

def track_activity(guild_id, user_id, stat_type, points_change):
    guild_id, user_id = str(guild_id), str(user_id)
    ensure_user_stats(guild_id, user_id)
    
    stats_storage[guild_id][user_id]["points"] += points_change
    if stat_type in stats_storage[guild_id][user_id]:
        stats_storage[guild_id][user_id][stat_type] += 1
        
    save_json(STATS_FILE, stats_storage)
    return stats_storage[guild_id][user_id]

async def advance_turn(guild_id, channel):
    guild_id = str(guild_id)
    if guild_id in active_games and len(active_games[guild_id]["players"]) > 0:
        game = active_games[guild_id]
        game["index"] = (game["index"] + 1) % len(game["players"])
        next_player_id = game["players"][game["index"]]
        await channel.send(f"🔄 **Turn Tracker:** It is now <@{next_player_id}>'s turn! Pick your fate using `/truth`, `/dare`, or `/random_tod`.")

# ==========================================
# 2. INTERACTIVE BUTTON UI (WITH REF TIMER)
# ==========================================
class GameActionView(discord.ui.View):
    def __init__(self, target_user: discord.Member, points_worth: int, game_type: str, show_timer: bool = False):
        super().__init__(timeout=None)
        self.target_user = target_user
        self.points_worth = points_worth
        self.game_type = game_type 
        
        if not show_timer:
            self.remove_item(self.start_timer_button)

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success, emoji="✅")
    async def complete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_user.id:
            return await interaction.response.send_message("❌ This is not your turn!", ephemeral=True)

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        user_stats = track_activity(interaction.guild_id, interaction.user.id, self.game_type, self.points_worth)
        
        embed = discord.Embed(
            description=f"🎉 **{interaction.user.display_name}** completed the task and earned **{self.points_worth} points**! (Total: {user_stats['points']} pts)",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed)
        await advance_turn(interaction.guild_id, interaction.channel)

    @discord.ui.button(label="Forfeit / Skip", style=discord.ButtonStyle.danger, emoji="❌")
    async def forfeit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_user.id:
            return await interaction.response.send_message("❌ This is not your turn!", ephemeral=True)

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        loss = abs(POINTS_MAP["forfeit"])
        user_stats = track_activity(interaction.guild_id, interaction.user.id, "forfeits", -loss)
        
        penalty = random.choice(PENALTIES) if PENALTIES else "Do 10 pushups."

        embed = discord.Embed(
            title="🚨 FORFEIT!",
            description=f"**{interaction.user.display_name}** chickened out! They lose **{loss} points** (Total: {user_stats['points']} pts).\n\n**PENALTY:** {penalty}",
            color=discord.Color.dark_red()
        )
        await interaction.followup.send(embed=embed)
        await advance_turn(interaction.guild_id, interaction.channel)

    @discord.ui.button(label="Start Timer", style=discord.ButtonStyle.secondary, emoji="⏱️")
    async def start_timer_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        await interaction.response.edit_message(view=self)
        
        duration = 60 
        end_time = int(time.time()) + duration
        
        timer_msg = await interaction.followup.send(f"⏳ **Timer Started!** You have until <t:{end_time}:R> to finish the dare!")
        
        await asyncio.sleep(duration)
        try:
            await timer_msg.reply(f"⏰ **TIME IS UP!** <@{self.target_user.id}>, did you complete it in time?")
        except Exception:
            pass

# ==========================================
# 3. BOT INITIALIZATION
# ==========================================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands globally.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# ==========================================
# 4. QUEUE & SYSTEM LOBBY COMMANDS
# ==========================================
@bot.tree.command(name="join_game", description="Join the Truth or Dare turn queue.")
async def join_game(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    user_id = str(interaction.user.id)
    
    if guild_id not in active_games:
        active_games[guild_id] = {"players": [], "index": 0}
        
    if user_id in active_games[guild_id]["players"]:
        return await interaction.response.send_message("You are already in the game!", ephemeral=True)
        
    active_games[guild_id]["players"].append(user_id)
    await interaction.response.send_message(f"✅ **{interaction.user.display_name}** joined the game! ({len(active_games[guild_id]['players'])} players in queue)")

@bot.tree.command(name="leave_game", description="Leave the current game queue.")
async def leave_game(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    user_id = str(interaction.user.id)
    
    if guild_id in active_games and user_id in active_games[guild_id]["players"]:
        active_games[guild_id]["players"].remove(user_id)
        if len(active_games[guild_id]["players"]) == 0:
            del active_games[guild_id]
            return await interaction.response.send_message("You left the game. The queue is now empty and the game has ended.")
        await interaction.response.send_message(f"👋 **{interaction.user.display_name}** left the game.")
    else:
        await interaction.response.send_message("You are not currently in a game.", ephemeral=True)

# ==========================================
# 5. USER STAT PROFILES & LEADERBOARDS
# ==========================================
@bot.tree.command(name="profile", description="View a player's Truth or Dare performance profile dashboard.")
async def profile(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    guild_id, user_id = str(interaction.guild_id), str(target.id)
    
    ensure_user_stats(guild_id, user_id)
    stats = stats_storage[guild_id][user_id]
    
    total_attempts = stats["truths_completed"] + stats["dares_completed"] + stats["challenges_completed"] + stats["forfeits"]
    chicken_ratio = 0.0
    if total_attempts > 0:
        chicken_ratio = (stats["forfeits"] / total_attempts) * 100
        
    embed = discord.Embed(title=f"📊 Game Profile: {target.display_name}", color=discord.Color.purple())
    embed.set_thumbnail(url=target.display_avatar.url)
    
    embed.add_field(name="🏆 Total Score", value=f"`{stats['points']} pts`", inline=True)
    embed.add_field(name="🐔 Chicken Ratio", value=f"`{chicken_ratio:.1f}%`", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    
    embed.add_field(name="🤫 Truths Mastered", value=f"💬 {stats['truths_completed']}", inline=True)
    embed.add_field(name="🔥 Dares Executed", value=f"⚡ {stats['dares_completed']}", inline=True)
    embed.add_field(name="⚔️ Challenges Won", value=f"👑 {stats['challenges_completed']}", inline=True)
    
    embed.set_footer(text=f"Total Actions Selected: {total_attempts}")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="See the top players in the server.")
async def leaderboard(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    
    if guild_id not in stats_storage or not stats_storage[guild_id]:
        return await interaction.response.send_message("No one has any points yet!")

    sorted_users = sorted(stats_storage[guild_id].items(), key=lambda item: item[1]["points"], reverse=True)
    
    board = ""
    for rank, (user_id, data) in enumerate(sorted_users[:10], 1):
        board += f"**{rank}.** <@{user_id}> — {data['points']} pts\n"
        
    embed = discord.Embed(title="🏆 Truth or Dare Leaderboard", description=board, color=discord.Color.gold())
    await interaction.response.send_message(embed=embed)

# ==========================================
# 6. ACTION CORE GAMEPLAY COMMANDS
# ==========================================
@bot.tree.command(name="truth", description="Get a random truth question.")
@app_commands.choices(rating=[
    app_commands.Choice(name="Normal", value="normal"),
    app_commands.Choice(name="Teen (Spicy)", value="teen"),
    app_commands.Choice(name="18+ (Extra Spicy)", value="18+")
])
async def truth(interaction: discord.Interaction, rating: app_commands.Choice[str]):
    guild_id = str(interaction.guild_id)
    ensure_guild_storage(guild_id)
    
    base_pool = DEFAULT_TRUTHS.get(rating.value, ["No default prompts loaded."])
    pool = base_pool.copy()
    pool.extend(custom_storage[guild_id]["truth"])
    
    selected_truth = random.choice(pool)
    points_worth = POINTS_MAP[rating.value]
    
    embed = discord.Embed(
        title=f"🤫 Truth ({rating.name}) [+{points_worth} pts]",
        description=f"**{interaction.user.mention}**, your question is:\n\n💬 *{selected_truth}*",
        color=discord.Color.blue()
    )
    view = GameActionView(target_user=interaction.user, points_worth=points_worth, game_type="truths_completed", show_timer=False)
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="dare", description="Get a random dare challenge.")
@app_commands.choices(rating=[
    app_commands.Choice(name="Normal", value="normal"),
    app_commands.Choice(name="Teen (Spicy)", value="teen"),
    app_commands.Choice(name="18+ (Extra Spicy)", value="18+")
])
@app_commands.choices(mode=[
    app_commands.Choice(name="In-Person", value="in_person"),
    app_commands.Choice(name="Online", value="online")
])
async def dare(interaction: discord.Interaction, rating: app_commands.Choice[str], mode: app_commands.Choice[str]):
    guild_id = str(interaction.guild_id)
    ensure_guild_storage(guild_id)
    
    base_pool = DEFAULT_DARES.get(rating.value, {}).get(mode.value, ["No default prompts loaded."])
    pool = base_pool.copy()
    pool.extend(custom_storage[guild_id]["dare"][mode.value])
    
    selected_dare = random.choice(pool)
    points_worth = POINTS_MAP[rating.value]
    
    embed = discord.Embed(
        title=f"🔥 Dare ({rating.name} | {mode.name}) [+{points_worth} pts]",
        description=f"**{interaction.user.mention}**, your dare is:\n\n⚡ *{selected_dare}*",
        color=discord.Color.red()
    )
    view = GameActionView(target_user=interaction.user, points_worth=points_worth, game_type="dares_completed", show_timer=True)
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="challenge", description="Get a high-stakes group challenge!")
async def challenge(interaction: discord.Interaction):
    if not CHALLENGES:
        return await interaction.response.send_message("❌ No challenges loaded inside configurations.")
        
    selected_challenge = random.choice(CHALLENGES)
    points_worth = POINTS_MAP["challenge"]
    
    embed = discord.Embed(
        title=f"⚔️ Epic Challenge Mode [+{points_worth} pts] ⚔️",
        description=f"**{interaction.user.mention}** has pulled a challenge:\n\n{selected_challenge}",
        color=discord.Color.gold()
    )
    view = GameActionView(target_user=interaction.user, points_worth=points_worth, game_type="challenges_completed", show_timer=True)
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="random_tod", description="Totally random! Picks Truth/Dare, Rating, and Mode for you.")
async def random_tod(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    ensure_guild_storage(guild_id)

    is_truth = random.choice([True, False])
    rating_val = random.choice(["normal", "teen", "18+"])
    rating_display_names = {"normal": "Normal", "teen": "Teen (Spicy)", "18+": "18+ (Extra Spicy)"}
    points_worth = POINTS_MAP[rating_val]
    
    if is_truth:
        base_pool = DEFAULT_TRUTHS.get(rating_val, ["No parameters configured."])
        pool = base_pool.copy()
        pool.extend(custom_storage[guild_id]["truth"])
        selected = random.choice(pool)
        
        embed = discord.Embed(
            title=f"🎲 Random Roll: 🤫 Truth ({rating_display_names[rating_val]}) [+{points_worth} pts]",
            description=f"**{interaction.user.mention}**, destiny has chosen a truth for you:\n\n💬 *{selected}*",
            color=discord.Color.blue()
        )
        view = GameActionView(target_user=interaction.user, points_worth=points_worth, game_type="truths_completed", show_timer=False)
    else:
        mode_val = random.choice(["in_person", "online"])
        mode_display_names = {"in_person": "In-Person", "online": "Online"}
        
        base_pool = DEFAULT_DARES.get(rating_val, {}).get(mode_val, ["No parameters configured."])
        pool = base_pool.copy()
        pool.extend(custom_storage[guild_id]["dare"][mode_val])
        selected = random.choice(pool)
        
        embed = discord.Embed(
            title=f"🎲 Random Roll: 🔥 Dare ({rating_display_names[rating_val]} | {mode_display_names[mode_val]}) [+{points_worth} pts]",
            description=f"**{interaction.user.mention}**, destiny has chosen a dare for you:\n\n⚡ *{selected}*",
            color=discord.Color.red()
        )
        view = GameActionView(target_user=interaction.user, points_worth=points_worth, game_type="dares_completed", show_timer=True)

    await interaction.response.send_message(embed=embed, view=view)


# ==========================================
# 7. CUSTOM CONTENT ADMIN COMMAND
# ==========================================
@bot.tree.command(name="add_custom", description="Add a server-specific custom Truth or Dare.")
@app_commands.choices(type=[
    app_commands.Choice(name="Truth", value="truth"),
    app_commands.Choice(name="Dare", value="dare")
])
@app_commands.choices(dare_mode=[
    app_commands.Choice(name="In-Person", value="in_person"),
    app_commands.Choice(name="Online", value="online")
])
async def add_custom(interaction: discord.Interaction, type: app_commands.Choice[str], text: str, dare_mode: app_commands.Choice[str] = None):
    guild_id = str(interaction.guild_id)
    ensure_guild_storage(guild_id)
    
    if type.value == "truth":
        custom_storage[guild_id]["truth"].append(text)
        confirm_msg = f"✅ Added to custom Truths: *\"{text}\"*"
    else:
        if not dare_mode:
            return await interaction.response.send_message("❌ You must specify a `dare_mode` (In-Person or Online) when adding a Dare!", ephemeral=True)
            
        custom_storage[guild_id]["dare"][dare_mode.value].append(text)
        confirm_msg = f"✅ Added to custom Dares ({dare_mode.name}): *\"{text}\"*"

    save_json(DATA_FILE, custom_storage)
    await interaction.response.send_message(confirm_msg, ephemeral=True)

# ==========================================
# APPLICATION ENGINE EXECUTION
# ==========================================
TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("Error: DISCORD_TOKEN not found in environment variables. Make sure your .env file is set up.")