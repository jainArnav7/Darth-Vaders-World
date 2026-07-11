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

ADMIN_ID = 839630790207995914  # Exclusive God Mode ID

POINTS_MAP = {
    "normal": 1,
    "teen": 2,
    "18+": 3,
    "challenge": 5,
    "forfeit": -2
}

SHOP_ITEMS = {
    "shield": {"name": "🛡️ Shield", "cost": 5, "desc": "Skip a turn without losing points."},
    "reverse": {"name": "🔄 Reverse Card", "cost": 10, "desc": "Force another active player to do your prompt instead."},
    "target": {"name": "🎯 Target", "cost": 15, "desc": "Bypass the queue order and pick who goes next."}
}

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

prompts_data = load_json(DEFAULT_PROMPTS_FILE)

DEFAULT_TRUTHS = prompts_data.get("truths", {})
DEFAULT_DARES = prompts_data.get("dares", {})
CHALLENGES = prompts_data.get("challenges", [])
PENALTIES = prompts_data.get("penalties", [])

custom_storage = load_json(DATA_FILE)
stats_storage = load_json(STATS_FILE)
active_games = {}

def ensure_guild_storage(guild_id):
    guild_id = str(guild_id)
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
            "forfeits": 0,
            "inventory": {"shield": 0, "reverse": 0, "target": 0}
        }
    if "inventory" not in stats_storage[guild_id][user_id]:
        stats_storage[guild_id][user_id]["inventory"] = {"shield": 0, "reverse": 0, "target": 0}

def track_activity(guild_id, user_id, stat_type, points_change):
    guild_id, user_id = str(guild_id), str(user_id)
    ensure_user_stats(guild_id, user_id)
    
    stats_storage[guild_id][user_id]["points"] += points_change
    if stat_type in stats_storage[guild_id][user_id]:
        stats_storage[guild_id][user_id][stat_type] += 1
        
    save_json(STATS_FILE, stats_storage)
    return stats_storage[guild_id][user_id]

# NON-REPEATING PROMPT ENGINE
def get_unique_prompt(guild_id, pool):
    guild_id = str(guild_id)
    if guild_id not in active_games:
        return random.choice(pool) # Fallback if rolled outside a lobby

    if "used_prompts" not in active_games[guild_id]:
        active_games[guild_id]["used_prompts"] = []

    # Filter out anything already tracked in this game session
    available = [p for p in pool if p not in active_games[guild_id]["used_prompts"]]

    if not available:
        # If pool is exhausted, clear ONLY these specific prompts from the used list to restock
        active_games[guild_id]["used_prompts"] = [p for p in active_games[guild_id]["used_prompts"] if p not in pool]
        available = pool

    selected = random.choice(available)
    active_games[guild_id]["used_prompts"].append(selected)
    return selected

async def advance_turn(guild_id, channel, override_next_player=None):
    guild_id = str(guild_id)
    if guild_id in active_games and len(active_games[guild_id]["players"]) > 0:
        game = active_games[guild_id]
        
        if override_next_player:
            player_str = str(override_next_player)
            if player_str in game["players"]:
                game["index"] = game["players"].index(player_str)
        else:
            game["index"] = (game["index"] + 1) % len(game["players"])
            
        next_player_id = game["players"][game["index"]]
        await channel.send(f"🔄 **Turn Tracker:** It is now <@{next_player_id}>'s turn! Pick your fate using `/truth`, `/dare`, or `/random_tod`.")

def get_multiplier_details():
    if random.random() < 0.05:
        mult = random.choice([2, 3])
        return mult, f"✨🎲 **GOLDEN TURN!** All stakes are multiplied by **x{mult}**! Big rewards or devastating failure! 🎲✨"
    return 1, ""

# ==========================================
# 2. INTERACTIVE BUTTON UI (WITH REROLL)
# ==========================================
class GameActionView(discord.ui.View):
    def __init__(self, target_user: discord.Member, points_worth: int, game_type: str, pool: list, desc_template: str, mult_msg: str, show_timer: bool = False, multiplier: int = 1):
        super().__init__(timeout=None)
        self.target_user = target_user
        self.points_worth = points_worth * multiplier
        self.game_type = game_type 
        self.multiplier = multiplier
        self.is_completed = False 
        
        # Reroll tracking data
        self.pool = pool
        self.desc_template = desc_template
        self.mult_msg = mult_msg
        self.has_rerolled = False
        
        if not show_timer:
            self.remove_item(self.start_timer_button)

    @discord.ui.button(label="Re-roll", style=discord.ButtonStyle.secondary, emoji="🎲")
    async def reroll_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_user.id:
            return await interaction.response.send_message("❌ This is not your turn!", ephemeral=True)
            
        if self.has_rerolled:
            return await interaction.response.send_message("❌ You can only re-roll once per turn!", ephemeral=True)
            
        self.has_rerolled = True
        button.disabled = True
        
        new_prompt = get_unique_prompt(interaction.guild_id, self.pool)
        
        embed = interaction.message.embeds[0]
        embed.description = self.desc_template.format(user=self.target_user.mention, prompt=new_prompt, mult_msg=self.mult_msg)
        
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="I'm Done (Needs Proof)", style=discord.ButtonStyle.primary, emoji="✋")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_user.id:
            return await interaction.response.send_message("❌ This is not your turn!", ephemeral=True)

        self.is_completed = True
        button.disabled = True
        self.reroll_button.disabled = True # Disable reroll after claiming
        button.label = "Waiting for Verification..."
        await interaction.response.edit_message(view=self)
        
        await interaction.followup.send(f"👀 **<@{self.target_user.id}> claims they finished!** Send your proof, then another active player must click **Verify** to award the points.")

    @discord.ui.button(label="Verify (Other Players)", style=discord.ButtonStyle.success, emoji="✅")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_completed:
            return await interaction.response.send_message("❌ The player hasn't marked this as done yet!", ephemeral=True)
            
        if interaction.user.id == self.target_user.id:
            return await interaction.response.send_message("❌ Nice try! You cannot verify your own task. Someone else must vouch for you.", ephemeral=True)

        guild_id_str = str(interaction.guild_id)
        user_id_str = str(interaction.user.id)
        
        if guild_id_str in active_games and len(active_games[guild_id_str]["players"]) > 0:
            if user_id_str not in active_games[guild_id_str]["players"]:
                return await interaction.response.send_message("❌ You cannot verify this because you are not currently in the game queue! Join the lobby first.", ephemeral=True)

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        user_stats = track_activity(interaction.guild_id, self.target_user.id, self.game_type, self.points_worth)
        
        embed = discord.Embed(
            description=f"🎉 **{self.target_user.display_name}**'s task was verified by **{interaction.user.display_name}**! They earned **{self.points_worth} points**! (Total: {user_stats['points']} pts)",
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

        loss = abs(POINTS_MAP["forfeit"]) * self.multiplier
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
intents.messages = True
intents.message_content = True
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
# ANONYMOUS SECRET PROMPTS (DM ENGINE)
# ==========================================
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if isinstance(message.channel, discord.DMChannel):
        text = message.content.strip()
        if text.startswith("truth:"):
            content = text[6:].strip()
            for guild in bot.guilds:
                ensure_guild_storage(guild.id)
                custom_storage[str(guild.id)]["truth"].append(content)
            save_json(DATA_FILE, custom_storage)
            await message.channel.send("🤫 **Anonymous Truth added successfully to all servers!** Nobody will know it was you.")
        elif text.startswith("dare_online:"):
            content = text[12:].strip()
            for guild in bot.guilds:
                ensure_guild_storage(guild.id)
                custom_storage[str(guild.id)]["dare"]["online"].append(content)
            save_json(DATA_FILE, custom_storage)
            await message.channel.send("⚡ **Anonymous Online Dare added successfully to all servers!**")
        elif text.startswith("dare_person:"):
            content = text[12:].strip()
            for guild in bot.guilds:
                ensure_guild_storage(guild.id)
                custom_storage[str(guild.id)]["dare"]["in_person"].append(content)
            save_json(DATA_FILE, custom_storage)
            await message.channel.send("👟 **Anonymous In-Person Dare added successfully to all servers!**")
        else:
            await message.channel.send(
                "🕵️‍♂️ **Anonymous Content Submission Box** 🕵️‍♂️\n"
                "To submit anonymous prompts without revealing your name in public commands, reply with exactly one of these prefixes:\n\n"
                "`truth: Your truth prompt here`\n"
                "`dare_online: Your online dare prompt here`\n"
                "`dare_person: Your in-person dare prompt here`"
            )
    await bot.process_commands(message)

# ==========================================
# 4. INTERACTIVE LOBBY SYSTEM
# ==========================================
def get_lobby_embed(guild_id):
    players = active_games.get(str(guild_id), {}).get("players", [])
    desc = "\n".join([f"🎮 <@{p}>" for p in players]) if players else "*Waiting for players to join...*"
    
    embed = discord.Embed(title="🕹️ Truth or Dare Lobby Queue", description=desc, color=discord.Color.blurple())
    embed.set_footer(text=f"Total Players: {len(players)} | Run /random_tod to start drawing!")
    return embed

class LobbyView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)

    @discord.ui.button(label="Join Game", style=discord.ButtonStyle.success, emoji="✅")
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        
        if self.guild_id not in active_games:
            active_games[self.guild_id] = {"players": [], "index": 0, "used_prompts": []}
            
        if user_id in active_games[self.guild_id]["players"]:
            return await interaction.response.send_message("You are already in the lobby!", ephemeral=True)
            
        active_games[self.guild_id]["players"].append(user_id)
        await interaction.response.edit_message(embed=get_lobby_embed(self.guild_id), view=self)

    @discord.ui.button(label="Leave Game", style=discord.ButtonStyle.danger, emoji="👋")
    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        
        if self.guild_id in active_games and user_id in active_games[self.guild_id]["players"]:
            active_games[self.guild_id]["players"].remove(user_id)
            if len(active_games[self.guild_id]["players"]) == 0:
                del active_games[self.guild_id]
            await interaction.response.edit_message(embed=get_lobby_embed(self.guild_id), view=self)
        else:
            await interaction.response.send_message("You are not in the lobby.", ephemeral=True)

@bot.tree.command(name="lobby", description="Open the interactive Game Lobby to join or leave the queue.")
async def lobby(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    view = LobbyView(guild_id)
    embed = get_lobby_embed(guild_id)
    await interaction.response.send_message(embed=embed, view=view)

# ==========================================
# 5. USER STAT PROFILES & LEADERBOARDS
# ==========================================
@bot.tree.command(name="reset_my_stats", description="Reset your personal points and inventory back to zero.")
async def reset_my_stats(interaction: discord.Interaction):
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    ensure_user_stats(guild_id, user_id)
    
    stats_storage[guild_id][user_id] = {
        "points": 0,
        "truths_completed": 0,
        "dares_completed": 0,
        "challenges_completed": 0,
        "forfeits": 0,
        "inventory": {"shield": 0, "reverse": 0, "target": 0}
    }
    save_json(STATS_FILE, stats_storage)
    await interaction.response.send_message("♻️ **Fresh Start!** Your points, stats, and inventory have been completely reset to zero.", ephemeral=True)

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
        
    titles = []
    if stats["dares_completed"] >= 10:
        titles.append("🔥 **The Daredevil** (10+ Dares Executed)")
    if stats["truths_completed"] >= 15:
        titles.append("🤫 **The Open Book** (15+ Truths Mastered)")
    if total_attempts >= 5 and chicken_ratio > 50.0:
        titles.append("🐔 **The Chicken** (Forfeit Ratio over 50%)")
        
    titles_display = "\n".join(titles) if titles else "*No unlocked titles yet*"

    embed = discord.Embed(title=f"📊 Game Profile: {target.display_name}", color=discord.Color.purple())
    embed.set_thumbnail(url=target.display_avatar.url)
    
    embed.add_field(name="🏆 Total Score", value=f"`{stats['points']} pts`", inline=True)
    embed.add_field(name="🐔 Chicken Ratio", value=f"`{chicken_ratio:.1f}%`", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    
    embed.add_field(name="🤫 Truths Mastered", value=f"💬 {stats['truths_completed']}", inline=True)
    embed.add_field(name="🔥 Dares Executed", value=f"⚡ {stats['dares_completed']}", inline=True)
    embed.add_field(name="⚔️ Challenges Won", value=f"👑 {stats['challenges_completed']}", inline=True)
    
    embed.add_field(name="🏆 Earned Titles & Achievements", value=titles_display, inline=False)
    
    inv = stats.get("inventory", {"shield": 0, "reverse": 0, "target": 0})
    inv_display = f"🛡️ Shields: `{inv.get('shield', 0)}` | 🔄 Reverses: `{inv.get('reverse', 0)}` | 🎯 Targets: `{inv.get('target', 0)}`"
    embed.add_field(name="🎒 Inventory Bag", value=inv_display, inline=False)
    
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
# 6. ECONOMY POINT SHOP & INVENTORY ITEMS ENGINE
# ==========================================
@bot.tree.command(name="shop", description="Open the item point economy store.")
async def shop(interaction: discord.Interaction):
    embed = discord.Embed(title="🛒 Point Economy Item Shop", description="Spend your hard-earned score to acquire rule-bending tactics!", color=discord.Color.blue())
    for key, data in SHOP_ITEMS.items():
        embed.add_field(name=f"{data['name']} — Cost: {data['cost']} pts", value=f"*{data['desc']}*\nUse via `/use_item item:{key}`", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="buy_item", description="Purchase an advantage item from the point shop.")
@app_commands.choices(item=[
    app_commands.Choice(name="🛡️ Shield (5 pts)", value="shield"),
    app_commands.Choice(name="🔄 Reverse Card (10 pts)", value="reverse"),
    app_commands.Choice(name="🎯 Target (15 pts)", value="target")
])
async def buy_item(interaction: discord.Interaction, item: app_commands.Choice[str]):
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    ensure_user_stats(guild_id, user_id)
    
    item_key = item.value
    cost = SHOP_ITEMS[item_key]["cost"]
    
    if stats_storage[guild_id][user_id]["points"] < cost:
        return await interaction.response.send_message(f"❌ You cannot afford this item! You need `{cost} pts` but only have `{stats_storage[guild_id][user_id]['points']} pts`.", ephemeral=True)
        
    stats_storage[guild_id][user_id]["points"] -= cost
    stats_storage[guild_id][user_id]["inventory"][item_key] += 1
    save_json(STATS_FILE, stats_storage)
    
    await interaction.response.send_message(f"🎒 Successfully bought 1x **{SHOP_ITEMS[item_key]['name']}**! Handled ledger balance: `{stats_storage[guild_id][user_id]['points']} pts` left.")

@bot.tree.command(name="use_item", description="Use an item from your bag inventory.")
@app_commands.choices(item=[
    app_commands.Choice(name="🛡️ Shield", value="shield"),
    app_commands.Choice(name="🔄 Reverse Card", value="reverse"),
    app_commands.Choice(name="🎯 Target", value="target")
])
async def use_item(interaction: discord.Interaction, item: app_commands.Choice[str], target_player: discord.Member = None):
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    ensure_user_stats(guild_id, user_id)
    
    item_key = item.value
    if stats_storage[guild_id][user_id]["inventory"].get(item_key, 0) <= 0:
        return await interaction.response.send_message(f"❌ You do not possess any **{SHOP_ITEMS[item_key]['name']}** inside your active inventory!", ephemeral=True)

    if guild_id not in active_games or len(active_games[guild_id]["players"]) == 0:
        return await interaction.response.send_message("❌ Items can only be activated while an active game lobby is running!", ephemeral=True)
        
    game = active_games[guild_id]
    current_turn_player = game["players"][game["index"]]
    
    if user_id != current_turn_player:
        return await interaction.response.send_message("❌ You can only activate items during your own active turn!", ephemeral=True)

    if item_key == "shield":
        stats_storage[guild_id][user_id]["inventory"]["shield"] -= 1
        save_json(STATS_FILE, stats_storage)
        await interaction.response.send_message(f"🛡️ **{interaction.user.display_name}** popped a Shield! Skipping turn without any point execution penalty.")
        await advance_turn(interaction.guild_id, interaction.channel)
        
    elif item_key == "reverse":
        if len(game["players"]) < 2:
            return await interaction.response.send_message("❌ Not enough players in queue to reverse this turn!", ephemeral=True)
        
        stats_storage[guild_id][user_id]["inventory"]["reverse"] -= 1
        save_json(STATS_FILE, stats_storage)
        
        prev_index = (game["index"] - 1) % len(game["players"])
        victim_id = game["players"][prev_index]
        
        await interaction.response.send_message(f"🔄 **REVERSE CARD PLAYED!** <@{user_id}> bounced the target tracking back to <@{victim_id}>! They must call a `/truth` or `/dare` immediately!")
        await advance_turn(interaction.guild_id, interaction.channel, override_next_player=victim_id)

    elif item_key == "target":
        if not target_player:
            return await interaction.response.send_message("❌ You must specify a `target_player` parameter to use this item!", ephemeral=True)
            
        target_id_str = str(target_player.id)
        if target_id_str not in game["players"]:
            return await interaction.response.send_message("❌ That targeted member is not inside the current game queue list!", ephemeral=True)
            
        stats_storage[guild_id][user_id]["inventory"]["target"] -= 1
        save_json(STATS_FILE, stats_storage)
        
        await interaction.response.send_message(f"🎯 **TARGET ACQUIRED!** <@{user_id}> hijacked the server pathing wheel and forced the spotlight onto <@{target_id_str}>!")
        await advance_turn(interaction.guild_id, interaction.channel, override_next_player=target_id_str)

# ==========================================
# 7. ACTION CORE GAMEPLAY COMMANDS
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
    
    selected_truth = get_unique_prompt(guild_id, pool)
    points_worth = POINTS_MAP[rating.value]
    
    mult, mult_msg = get_multiplier_details()
    desc_template = "{mult_msg}\n\n**{user}**, your question is:\n\n💬 *{prompt}*"
    
    embed = discord.Embed(
        title=f"🤫 Truth ({rating.name}) [+{points_worth * mult} pts]",
        description=desc_template.format(user=interaction.user.mention, prompt=selected_truth, mult_msg=mult_msg),
        color=discord.Color.blue()
    )
    view = GameActionView(target_user=interaction.user, points_worth=points_worth, game_type="truths_completed", pool=pool, desc_template=desc_template, mult_msg=mult_msg, show_timer=False, multiplier=mult)
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
    
    selected_dare = get_unique_prompt(guild_id, pool)
    points_worth = POINTS_MAP[rating.value]
    
    mult, mult_msg = get_multiplier_details()
    desc_template = "{mult_msg}\n\n**{user}**, your dare is:\n\n⚡ *{prompt}*"
    
    embed = discord.Embed(
        title=f"🔥 Dare ({rating.name} | {mode.name}) [+{points_worth * mult} pts]",
        description=desc_template.format(user=interaction.user.mention, prompt=selected_dare, mult_msg=mult_msg),
        color=discord.Color.red()
    )
    view = GameActionView(target_user=interaction.user, points_worth=points_worth, game_type="dares_completed", pool=pool, desc_template=desc_template, mult_msg=mult_msg, show_timer=True, multiplier=mult)
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="challenge", description="Get a high-stakes group challenge!")
async def challenge(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    if not CHALLENGES:
        return await interaction.response.send_message("❌ No challenges loaded inside configurations.")
        
    selected_challenge = get_unique_prompt(guild_id, CHALLENGES)
    points_worth = POINTS_MAP["challenge"]
    
    mult, mult_msg = get_multiplier_details()
    desc_template = "{mult_msg}\n\n**{user}** has pulled a challenge:\n\n{prompt}"
    
    embed = discord.Embed(
        title=f"⚔️ Epic Challenge Mode [+{points_worth * mult} pts] ⚔️",
        description=desc_template.format(user=interaction.user.mention, prompt=selected_challenge, mult_msg=mult_msg),
        color=discord.Color.gold()
    )
    view = GameActionView(target_user=interaction.user, points_worth=points_worth, game_type="challenges_completed", pool=CHALLENGES, desc_template=desc_template, mult_msg=mult_msg, show_timer=True, multiplier=mult)
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="random_tod", description="Totally random! Picks Truth/Dare, Rating, and Mode for you.")
async def random_tod(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    ensure_guild_storage(guild_id)

    is_truth = random.choice([True, False])
    rating_val = random.choice(["normal", "teen", "18+"])
    rating_display_names = {"normal": "Normal", "teen": "Teen (Spicy)", "18+": "18+ (Extra Spicy)"}
    points_worth = POINTS_MAP[rating_val]
    
    mult, mult_msg = get_multiplier_details()
    
    if is_truth:
        base_pool = DEFAULT_TRUTHS.get(rating_val, ["No parameters configured."])
        pool = base_pool.copy()
        pool.extend(custom_storage[guild_id]["truth"])
        selected = get_unique_prompt(guild_id, pool)
        
        desc_template = "{mult_msg}\n\n**{user}**, destiny has chosen a truth for you:\n\n💬 *{prompt}*"
        embed = discord.Embed(
            title=f"🎲 Random Roll: 🤫 Truth ({rating_display_names[rating_val]}) [+{points_worth * mult} pts]",
            description=desc_template.format(user=interaction.user.mention, prompt=selected, mult_msg=mult_msg),
            color=discord.Color.blue()
        )
        view = GameActionView(target_user=interaction.user, points_worth=points_worth, game_type="truths_completed", pool=pool, desc_template=desc_template, mult_msg=mult_msg, show_timer=False, multiplier=mult)
    else:
        mode_val = random.choice(["in_person", "online"])
        mode_display_names = {"in_person": "In-Person", "online": "Online"}
        
        base_pool = DEFAULT_DARES.get(rating_val, {}).get(mode_val, ["No parameters configured."])
        pool = base_pool.copy()
        pool.extend(custom_storage[guild_id]["dare"][mode_val])
        selected = get_unique_prompt(guild_id, pool)
        
        desc_template = "{mult_msg}\n\n**{user}**, destiny has chosen a dare for you:\n\n⚡ *{prompt}*"
        embed = discord.Embed(
            title=f"🎲 Random Roll: 🔥 Dare ({rating_display_names[rating_val]} | {mode_display_names[mode_val]}) [+{points_worth * mult} pts]",
            description=desc_template.format(user=interaction.user.mention, prompt=selected, mult_msg=mult_msg),
            color=discord.Color.red()
        )
        view = GameActionView(target_user=interaction.user, points_worth=points_worth, game_type="dares_completed", pool=pool, desc_template=desc_template, mult_msg=mult_msg, show_timer=True, multiplier=mult)

    await interaction.response.send_message(embed=embed, view=view)


# ==========================================
# 8. GOD MODE ADMIN COMMANDS
# ==========================================
class AdminPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Nuke Server Economy", style=discord.ButtonStyle.danger, emoji="💥")
    async def nuke_economy(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild_id)
        if guild_id in stats_storage:
            stats_storage[guild_id] = {}
            save_json(STATS_FILE, stats_storage)
        await interaction.response.send_message("💥 Database wiped. Server economy has been permanently deleted.", ephemeral=True)

    @discord.ui.button(label="Force End Lobby", style=discord.ButtonStyle.danger, emoji="🛑")
    async def end_lobby(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild_id)
        if guild_id in active_games:
            del active_games[guild_id]
        await interaction.response.send_message("🛑 The active game lobby queue was forcefully terminated.", ephemeral=True)

@bot.tree.command(name="admin_panel", description="[ADMIN ONLY] Open the Developer God Mode panel.")
async def admin_panel(interaction: discord.Interaction):
    if interaction.user.id != ADMIN_ID:
        return await interaction.response.send_message("❌ Access Denied: You do not have Developer God Mode permissions.", ephemeral=True)
    
    embed = discord.Embed(
        title="🛠️ God Mode: Admin Panel", 
        description="Welcome back, Creator. What global overrides would you like to execute?", 
        color=discord.Color.dark_theme()
    )
    await interaction.response.send_message(embed=embed, view=AdminPanelView(), ephemeral=True)

# ==========================================
# 9. CUSTOM CONTENT ADMIN COMMAND
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