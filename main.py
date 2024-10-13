import os 
import re
from collections import deque
import nextcord
from nextcord.ui import View, Select
from nextcord.ext import commands
import mafic
from dotenv import load_dotenv
import random
from collections import Counter
from mafic import NodePool, Player, Playlist, Track, TrackEndEvent, SearchType,TrackStartEvent
from asyncio import Lock

load_dotenv()
def is_youtube_url(url):
    # YouTube URL patterns
    patterns = [
        r'^https?://(?:www\.)?youtube\.com/watch\?v=[\w-]+',
        r'^https?://(?:www\.)?youtu\.be/[\w-]+',
    ]
    return any(re.match(pattern, url) for pattern in patterns)

TESTING_GUILD_ID = int(os.getenv("DISCORD_GUILD"))

class MyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.pool = mafic.NodePool(self)
        self.loop.create_task(self.add_nodes())
        self.music_queues = {}
        self.text_channels = {}  # Store text channels for each guild
        self.play_locks = {}
        self.current_song = {}
        self.play_history = {}
        self.recommendation_enabled = {}
        self.recommendation_history = {}  # New: Store recommendation history for each guild
        self.max_recommendation_history = 100  # Adjust this value as needed
    async def add_nodes(self):
        await self.pool.create_node(
            host="127.0.0.1",
            port=2333,
            label="MAIN",
            password="youshallnotpass",
        )

bot = MyBot(intents=nextcord.Intents(guilds=True, voice_states=True))

@bot.event
async def on_ready():
    print(f'We have logged in as {bot.user}')



@bot.listen("on_track_start")
async def on_track_start(event: TrackStartEvent):
    player = event.player
    track = event.track
    guild_id = player.guild.id
    bot.current_song[guild_id] = track
    
    # Record play history
    if guild_id not in bot.play_history:
        bot.play_history[guild_id] = []
    bot.play_history[guild_id].append(track.author)
    
    # Keep only the last 100 played songs
    bot.play_history[guild_id] = bot.play_history[guild_id][-100:]
    
    if guild_id in bot.text_channels:
        embed = nextcord.Embed(title="Now Playing", color=nextcord.Color.green())
        embed.add_field(name="Title", value=track.title, inline=False)
        embed.add_field(name="Author", value=track.author, inline=False)
        await bot.text_channels[guild_id].send(embed=embed)
    
    # Check if recommendations are needed
    await check_and_recommend(player, guild_id)

@bot.listen("on_track_end")
async def on_track_end(event: TrackEndEvent):
    player = event.player
    guild_id = player.guild.id
    
    bot.current_song.pop(guild_id, None)  # Clear the current song
    
    # Manage recommendation history
    manage_recommendation_history(guild_id)
    
    # Check if there are more tracks in the queue
    if guild_id in bot.music_queues and bot.music_queues[guild_id]:
        await play_next(player)
    else:
        if guild_id in bot.text_channels:
            embed = nextcord.Embed(title="Playback Finished", color=nextcord.Color.blue())
            embed.add_field(name="Message", value="Queue is empty. Playback finished.", inline=False)
            await bot.text_channels[guild_id].send(embed=embed)
        await player.disconnect()

async def play_next(player: mafic.Player):
    guild_id = player.guild.id
    if not player.connected:
        if guild_id in bot.music_queues:
            bot.music_queues[guild_id].clear()
        return

    if guild_id in bot.music_queues and bot.music_queues[guild_id]:
        next_track = bot.music_queues[guild_id].popleft()
        try:
            await player.play(next_track)
            bot.current_song[guild_id] = next_track
            print(f"Started playing: {next_track.title}")
        except Exception as e:
            print(f"Error playing track: {e}")
            if guild_id in bot.text_channels:
                embed = nextcord.Embed(title="Playback Error", color=nextcord.Color.red())
                embed.add_field(name="Error", value=f"Error playing track: {e}", inline=False)
                await bot.text_channels[guild_id].send(embed=embed)
            await play_next(player)
    else:
        if guild_id in bot.text_channels:
            embed = nextcord.Embed(title="Playback Finished", color=nextcord.Color.blue())
            embed.add_field(name="Message", value="Queue is empty. Playback finished.", inline=False)
            await bot.text_channels[guild_id].send(embed=embed)
        await player.disconnect()
    
    # Check for recommendations after playing a track
    await check_and_recommend(player, guild_id)

async def create_timeout_handler(inter: nextcord.Interaction, player: mafic.Player):
    async def on_timeout():
        # Check if the player is still in use
        if not player.current and player.connected:
            guild_id = inter.guild_id
            if guild_id in bot.music_queues and not bot.music_queues[guild_id]:
                await player.disconnect()
                if guild_id in bot.text_channels:
                    await bot.text_channels[guild_id].send("Search timed out. Disconnected from the voice channel.")
    
    return on_timeout
@bot.slash_command(description="Play music or search for tracks", dm_permission=False, guild_ids=[TESTING_GUILD_ID])
async def play(inter: nextcord.Interaction, query: str):
    if not inter.user.voice:
        embed = nextcord.Embed(title="Error", color=nextcord.Color.red())
        embed.add_field(name="Message", value="You need to be in a voice channel!", inline=False)
        return await inter.send(embed=embed)
    
    if inter.guild.voice_client and inter.guild.voice_client.channel != inter.user.voice.channel:
        embed = nextcord.Embed(title="Error", color=nextcord.Color.red())
        embed.add_field(name="Message", value="I'm already in a different voice channel. Please join my channel or use the stop command first.", inline=False)
        return await inter.send(embed=embed)
    
    bot.text_channels[inter.guild_id] = inter.channel

    if not inter.guild.voice_client:
        try:
            player = await inter.user.voice.channel.connect(cls=mafic.Player)
        except Exception as e:
            embed = nextcord.Embed(title="Error", color=nextcord.Color.red())
            embed.add_field(name="Message", value=f"Failed to connect to voice channel: {str(e)}", inline=False)
            return await inter.send(embed=embed)
    else:
        player = inter.guild.voice_client

    if inter.guild_id not in bot.music_queues:
        bot.music_queues[inter.guild_id] = deque()

    try:
        if is_youtube_url(query):
            results = await player.fetch_tracks(query)
        else:
            results = await player.fetch_tracks(query, search_type=mafic.SearchType.YOUTUBE)
    except Exception as e:
        embed = nextcord.Embed(title="Error", color=nextcord.Color.red())
        embed.add_field(name="Message", value=f"An error occurred while fetching tracks: {str(e)}", inline=False)
        return await inter.send(embed=embed)

    if not results:
        embed = nextcord.Embed(title="No Results", color=nextcord.Color.yellow())
        embed.add_field(name="Message", value="No tracks found.", inline=False)
        return await inter.send(embed=embed)

    if isinstance(results, mafic.Playlist):
        for track in results.tracks:
            bot.music_queues[inter.guild_id].append(track)
        embed = nextcord.Embed(title="Playlist Added", color=nextcord.Color.green())
        embed.add_field(name="Playlist Name", value=results.name, inline=False)
        embed.add_field(name="Tracks Added", value=str(len(results.tracks)), inline=False)
        await inter.send(embed=embed)
        if inter.guild_id not in bot.current_song:
            await play_next(player)
    elif is_youtube_url(query) or len(results) == 1:
        track = results[0]
        bot.music_queues[inter.guild_id].append(track)
        embed = nextcord.Embed(title="Track Added", color=nextcord.Color.green())
        embed.add_field(name="Title", value=track.title, inline=False)
        embed.add_field(name="Author", value=track.author, inline=False)
        await inter.send(embed=embed)
        if inter.guild_id not in bot.current_song:
            await play_next(player)
    else:
        options = [nextcord.SelectOption(label=f"{i+1}. {track.title[:50]}", description=f"By {track.author[:50]}", value=str(i)) for i, track in enumerate(results[:10])]
        select = nextcord.ui.Select(placeholder="Choose a track...", options=options)

        async def select_callback(interaction: nextcord.Interaction):
            selected_index = int(select.values[0])
            selected_track = results[selected_index]
            bot.music_queues[interaction.guild_id].append(selected_track)
            embed = nextcord.Embed(title="Track Added", color=nextcord.Color.green())
            embed.add_field(name="Title", value=selected_track.title, inline=False)
            embed.add_field(name="Author", value=selected_track.author, inline=False)
            await interaction.response.send_message(embed=embed)
            if interaction.guild_id not in bot.current_song:
                await play_next(player)

        select.callback = select_callback
        view = nextcord.ui.View(timeout=60)
        view.add_item(select)
        view.on_timeout = await create_timeout_handler(inter, player)

        embed = nextcord.Embed(title="Track Selection", color=nextcord.Color.blue())
        embed.add_field(name="Action", value="Please select a track to add to the queue:", inline=False)
        await inter.send(embed=embed, view=view)

@bot.slash_command(description="Play music or search for tracks (add to front of queue)", dm_permission=False, guild_ids=[TESTING_GUILD_ID])
async def playnext(inter: nextcord.Interaction, query: str):
    if not inter.user.voice:
        embed = nextcord.Embed(title="Error", color=nextcord.Color.red())
        embed.add_field(name="Message", value="You need to be in a voice channel!", inline=False)
        return await inter.send(embed=embed)
    
    if inter.guild.voice_client and inter.guild.voice_client.channel != inter.user.voice.channel:
        embed = nextcord.Embed(title="Error", color=nextcord.Color.red())
        embed.add_field(name="Message", value="I'm already in a different voice channel. Please join my channel or use the stop command first.", inline=False)
        return await inter.send(embed=embed)
    
    bot.text_channels[inter.guild_id] = inter.channel

    if not inter.guild.voice_client:
        try:
            player = await inter.user.voice.channel.connect(cls=mafic.Player)
        except Exception as e:
            embed = nextcord.Embed(title="Error", color=nextcord.Color.red())
            embed.add_field(name="Message", value=f"Failed to connect to voice channel: {str(e)}", inline=False)
            return await inter.send(embed=embed)
    else:
        player = inter.guild.voice_client

    if inter.guild_id not in bot.music_queues:
        bot.music_queues[inter.guild_id] = deque()

    try:
        if is_youtube_url(query):
            results = await player.fetch_tracks(query)
        else:
            results = await player.fetch_tracks(query, search_type=mafic.SearchType.YOUTUBE)
    except Exception as e:
        embed = nextcord.Embed(title="Error", color=nextcord.Color.red())
        embed.add_field(name="Message", value=f"An error occurred while fetching tracks: {str(e)}", inline=False)
        return await inter.send(embed=embed)

    if not results:
        embed = nextcord.Embed(title="No Results", color=nextcord.Color.yellow())
        embed.add_field(name="Message", value="No tracks found.", inline=False)
        return await inter.send(embed=embed)

    if isinstance(results, mafic.Playlist):
        for track in reversed(results.tracks):
            bot.music_queues[inter.guild_id].appendleft(track)
        embed = nextcord.Embed(title="Playlist Added", color=nextcord.Color.green())
        embed.add_field(name="Playlist Name", value=results.name, inline=False)
        embed.add_field(name="Tracks Added", value=str(len(results.tracks)), inline=False)
        embed.add_field(name="Position", value="Next in queue", inline=False)
        await inter.send(embed=embed)
        if inter.guild_id not in bot.current_song:
            await play_next(player)
    elif is_youtube_url(query) or len(results) == 1:
        track = results[0]
        bot.music_queues[inter.guild_id].appendleft(track)
        embed = nextcord.Embed(title="Track Added", color=nextcord.Color.green())
        embed.add_field(name="Title", value=track.title, inline=False)
        embed.add_field(name="Author", value=track.author, inline=False)
        embed.add_field(name="Position", value="Next in queue", inline=False)
        await inter.send(embed=embed)
        if inter.guild_id not in bot.current_song:
            await play_next(player)
    else:
        options = [nextcord.SelectOption(label=f"{i+1}. {track.title[:50]}", description=f"By {track.author[:50]}", value=str(i)) for i, track in enumerate(results[:10])]
        select = nextcord.ui.Select(placeholder="Choose a track...", options=options)

        async def select_callback(interaction: nextcord.Interaction):
            selected_index = int(select.values[0])
            selected_track = results[selected_index]
            bot.music_queues[interaction.guild_id].appendleft(selected_track)
            embed = nextcord.Embed(title="Track Added", color=nextcord.Color.green())
            embed.add_field(name="Title", value=selected_track.title, inline=False)
            embed.add_field(name="Author", value=selected_track.author, inline=False)
            embed.add_field(name="Position", value="Next in queue", inline=False)
            await interaction.response.send_message(embed=embed)
            if interaction.guild_id not in bot.current_song:
                await play_next(player)

        select.callback = select_callback
        view = nextcord.ui.View(timeout=60)
        view.add_item(select)
        view.on_timeout = await create_timeout_handler(inter, player)

        embed = nextcord.Embed(title="Track Selection", color=nextcord.Color.blue())
        embed.add_field(name="Action", value="Please select a track to play next:", inline=False)
        await inter.send(embed=embed, view=view)
@bot.slash_command(description="Stop the music and clear the queue", dm_permission=False, guild_ids=[TESTING_GUILD_ID])
async def stop(inter: nextcord.Interaction):
    if not inter.guild.voice_client or not isinstance(inter.guild.voice_client, mafic.Player):
        embed = nextcord.Embed(title="Error", color=nextcord.Color.red())
        embed.add_field(name="Message", value="I'm not playing anything right now.", inline=False)
        return await inter.send(embed=embed)

    player = inter.guild.voice_client
    if player.connected:
        bot.music_queues[inter.guild_id].clear()
        await player.stop()
        await player.disconnect()
        
        embed = nextcord.Embed(title="Playback Stopped", color=nextcord.Color.blue())
        embed.add_field(name="Action", value="Stopped the music, cleared the queue, and disconnected from the voice channel.", inline=False)
        
        await inter.send(embed=embed)
    else:
        embed = nextcord.Embed(title="Error", color=nextcord.Color.red())
        embed.add_field(name="Message", value="The player is not connected to a voice channel.", inline=False)
        await inter.send(embed=embed)

@bot.slash_command(description="Clear the queue without stopping the current track", dm_permission=False, guild_ids=[TESTING_GUILD_ID])
async def clear(inter: nextcord.Interaction):
    if inter.guild_id not in bot.music_queues or not bot.music_queues[inter.guild_id]:
        embed = nextcord.Embed(title="Queue Status", color=nextcord.Color.blue())
        embed.add_field(name="Message", value="The queue is already empty.", inline=False)
        return await inter.send(embed=embed)

    bot.music_queues[inter.guild_id].clear()
    
    embed = nextcord.Embed(title="Queue Cleared", color=nextcord.Color.green())
    embed.add_field(name="Action", value="Cleared the queue. The current track (if any) will continue playing.", inline=False)
    
    await inter.send(embed=embed)

@bot.slash_command(description="Pause the current track", dm_permission=False, guild_ids=[TESTING_GUILD_ID])
async def pause(inter: nextcord.Interaction):
    if not inter.guild.voice_client or not isinstance(inter.guild.voice_client, mafic.Player):
        return await inter.send("I'm not playing anything right now.")

    player = inter.guild.voice_client
    if player.connected and player.current:
        if player.paused:
            return await inter.send("The player is already paused.")
        await player.pause()
        await inter.send("Paused the current track.")
    else:
        await inter.send("Unable to pause. No track is currently playing.")

@bot.slash_command(description="Resume the paused track", dm_permission=False, guild_ids=[TESTING_GUILD_ID])
async def resume(inter: nextcord.Interaction):
    if not inter.guild.voice_client or not isinstance(inter.guild.voice_client, mafic.Player):
        return await inter.send("I'm not playing anything right now.")

    player = inter.guild.voice_client
    if player.connected and player.current:
        if not player.paused:
            return await inter.send("The player is not paused.")
        await player.resume()
        await inter.send("Resumed the current track.")
    else:
        await inter.send("Unable to resume. No track is currently playing.")

@bot.slash_command(description="Skip the current track", dm_permission=False, guild_ids=[TESTING_GUILD_ID])
async def skip(inter: nextcord.Interaction):
    if not inter.guild.voice_client or not isinstance(inter.guild.voice_client, mafic.Player):
        return await inter.send("I'm not playing anything right now.")

    player = inter.guild.voice_client
    if player.current:
        await player.stop()
        await inter.send("Skipped the current track.")
    else:
        await inter.send("No track is currently playing.")


@bot.slash_command(description="Clear the entire music queue", dm_permission=False, guild_ids=[TESTING_GUILD_ID])
async def clear_queue(inter: nextcord.Interaction):
    if inter.guild_id not in bot.music_queues or not bot.music_queues[inter.guild_id]:
        return await inter.send("The queue is already empty.")

    bot.music_queues[inter.guild_id].clear()
    await inter.send("The queue has been cleared. The current track (if any) will continue playing.")
@bot.slash_command(description="Delete a specific track from the queue", dm_permission=False, guild_ids=[TESTING_GUILD_ID])
async def delete_from_queue(inter: nextcord.Interaction):
    if inter.guild_id not in bot.music_queues or not bot.music_queues[inter.guild_id]:
        return await inter.send("The queue is empty.")

    queue = bot.music_queues[inter.guild_id]
    options = [
        nextcord.SelectOption(
            label=f"{i+1}. {track.title[:50]}",
            description=f"By {track.author[:50]}",
            value=str(i)
        ) for i, track in enumerate(queue)
    ]

    if len(options) > 25:  # Discord has a limit of 25 options in a select menu
        options = options[:25]
        await inter.send("Only showing the first 25 tracks due to Discord limitations.")

    select = nextcord.ui.Select(
        placeholder="Choose a track to delete...",
        options=options
    )

    async def select_callback(interaction: nextcord.Interaction):
        selected_index = int(select.values[0])
        deleted_track = queue[selected_index]
        del queue[selected_index]
        await interaction.response.send_message(f"Removed '{deleted_track.title}' from the queue.")

    select.callback = select_callback
    view = nextcord.ui.View(timeout=60)
    view.add_item(select)

    await inter.send("Select a track to remove from the queue:", view=view)

    async def on_timeout():
        await inter.edit_original_message(content="The selection timed out.", view=None)

    view.on_timeout = on_timeout
@bot.slash_command(description="Show information about the currently playing track", dm_permission=False, guild_ids=[TESTING_GUILD_ID])
async def now_playing(inter: nextcord.Interaction):
    if not inter.guild.voice_client or not isinstance(inter.guild.voice_client, mafic.Player):
        return await inter.send("I'm not playing anything right now.")

    player = inter.guild.voice_client
    if not player.current:
        return await inter.send("No track is currently playing.")

    track = player.current
    duration = format_duration(track.length)
    position = format_duration(player.position)

    embed = nextcord.Embed(title="Now Playing", color=nextcord.Color.blue())
    embed.add_field(name="Title", value=track.title, inline=False)
    embed.add_field(name="Author", value=track.author, inline=False)
    embed.add_field(name="Duration", value=f"{position} / {duration}", inline=False)
    
    if track.uri:
        embed.add_field(name="Link", value=f"[Click here]({track.uri})", inline=False)

    await inter.send(embed=embed)

def format_duration(duration_ms: int) -> str:
    seconds = duration_ms // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes:02d}:{seconds:02d}"
@bot.slash_command(description="Set the volume of the player", dm_permission=False, guild_ids=[TESTING_GUILD_ID])
async def volume(inter: nextcord.Interaction, volume: int):
    if not inter.guild.voice_client or not isinstance(inter.guild.voice_client, mafic.Player):
        return await inter.send("I'm not playing anything right now.")

    player = inter.guild.voice_client
    if 0 <= volume <= 1000:
        await player.set_volume(volume)
        await inter.send(f"Set the volume to {volume}%")
    else:
        await inter.send("Volume must be between 0 and 1000")
import random

@bot.slash_command(description="Shuffle the current queue", dm_permission=False, guild_ids=[TESTING_GUILD_ID])
async def shuffle(inter: nextcord.Interaction):
    if inter.guild_id not in bot.music_queues or len(bot.music_queues[inter.guild_id]) < 2:
        return await inter.send("The queue needs at least two tracks to shuffle.")

    queue = bot.music_queues[inter.guild_id]
    
    # Convert the deque to a list, shuffle it, and convert back to deque
    queue_list = list(queue)
    random.shuffle(queue_list)
    bot.music_queues[inter.guild_id] = deque(queue_list)

    # Create an embed to display the shuffled queue
    embed = nextcord.Embed(title="Queue Shuffled", color=nextcord.Color.green())
    
    # Display the current track (if any) and the first 10 tracks of the shuffled queue
    track_list = []
    current_track = bot.current_song.get(inter.guild_id)
    if current_track:
        track_list.append(f"Currently playing: {current_track.title} - {current_track.author}")
    
    for i, track in enumerate(queue_list):
        if i < 10:  # Show up to 10 tracks from the queue
            duration = format_duration(track.length)
            track_list.append(f"{i+1}. {track.title} - {track.author} ({duration})")
        else:
            break

    if track_list:
        embed.add_field(name="Current Track and Queue", value="\n".join(track_list), inline=False)
        if len(queue_list) > 10:
            embed.add_field(name="", value=f"And {len(queue_list) - 10} more...", inline=False)

    total_tracks = len(queue_list) + (1 if current_track else 0)
    embed.add_field(name="Queue Info", value=f"Total tracks: {total_tracks}\n"
                                             f"Tracks shuffled: {len(queue_list)}", inline=False)

    await inter.send(embed=embed)
@bot.slash_command(description="Show the current queue", dm_permission=False, guild_ids=[TESTING_GUILD_ID])
async def queue(inter: nextcord.Interaction):
    if inter.guild_id not in bot.music_queues and inter.guild_id not in bot.current_song:
        return await inter.send("The queue is empty and no song is currently playing.")

    embed = nextcord.Embed(title="Current Queue", color=nextcord.Color.blue())

    # Add current track information
    current_track = bot.current_song.get(inter.guild_id)
    if current_track:
        player = inter.guild.voice_client
        if player and isinstance(player, mafic.Player):
            current_position = format_duration(player.position)
            current_duration = format_duration(current_track.length)
            embed.add_field(name="Now Playing", value=f"{current_track.title} - {current_track.author}\n"
                                                      f"Duration: {current_position} / {current_duration}", inline=False)

    # Add queued tracks
    queue = bot.music_queues.get(inter.guild_id, [])
    track_list = []
    total_duration = 0
    for i, track in enumerate(queue):
        duration = format_duration(track.length)
        if i < 10:  # Show only first 10 tracks to avoid hitting Discord's character limit
            track_list.append(f"{i+1}. {track.title} - {track.author} ({duration})")
        total_duration += track.length

    if track_list:
        embed.add_field(name="Next in Queue", value="\n".join(track_list), inline=False)
        if len(queue) > 10:
            embed.add_field(name="", value=f"And {len(queue) - 10} more...", inline=False)
    else:
        embed.add_field(name="Next in Queue", value="No tracks in queue", inline=False)

    # Add total queue information
    total_tracks = len(queue)
    total_duration_formatted = format_duration(total_duration)
    embed.add_field(name="Queue Info", value=f"Total tracks in queue: {total_tracks}\n"
                                             f"Total duration of queue: {total_duration_formatted}", inline=False)

    await inter.send(embed=embed)
# Add this new command to toggle recommendations
@bot.slash_command(description="Toggle automatic song recommendations", dm_permission=False, guild_ids=[TESTING_GUILD_ID])
async def recommend(inter: nextcord.Interaction):
    guild_id = inter.guild_id
    bot.recommendation_enabled[guild_id] = not bot.recommendation_enabled.get(guild_id, False)
    status = "enabled" if bot.recommendation_enabled[guild_id] else "disabled"
    
    embed = nextcord.Embed(title="Recommendation Settings", color=nextcord.Color.blue())
    embed.add_field(name="Status", value=f"Automatic song recommendations are now {status}.", inline=False)
    
    await inter.send(embed=embed)
# Modify the check_and_recommend function
async def check_and_recommend(player: mafic.Player, guild_id: int):
    if (bot.recommendation_enabled.get(guild_id, False) and 
        len(bot.music_queues[guild_id]) <= 1 and 
        guild_id in bot.play_history and 
        bot.play_history[guild_id]):
        
        # Initialize recommendation history for the guild if it doesn't exist
        if guild_id not in bot.recommendation_history:
            bot.recommendation_history[guild_id] = deque(maxlen=bot.max_recommendation_history)
        
        # Get the most common authors from play history
        author_counts = Counter(bot.play_history[guild_id])
        common_authors = [author for author, _ in author_counts.most_common()]
        
        # Randomly select up to 10 authors (or all if less than 10)
        num_authors = min(10, len(common_authors))
        selected_authors = random.sample(common_authors, num_authors)
        
        recommended_tracks = 0
        added_tracks = set()  # To keep track of added tracks and avoid duplicates
        
        for author in selected_authors:
            if recommended_tracks >= 10:
                break
            
            query = f"{author} music"
            try:
                results = await player.fetch_tracks(query, search_type=mafic.SearchType.YOUTUBE)
                if results:
                    for track in results:
                        track_id = (track.title, track.author)
                        # Check if the track is not in recommendation history, not in added_tracks, and not in the current queue
                        if (track_id not in bot.recommendation_history[guild_id] and
                            track_id not in added_tracks and
                            not any(t.title == track.title and t.author == track.author for t in bot.music_queues[guild_id])):
                            
                            bot.music_queues[guild_id].append(track)
                            added_tracks.add(track_id)
                            bot.recommendation_history[guild_id].append(track_id)
                            recommended_tracks += 1
                            if guild_id in bot.text_channels:
                                embed = nextcord.Embed(title="Recommended Track Added", color=nextcord.Color.green())
                                embed.add_field(name="Title", value=track.title, inline=False)
                                embed.add_field(name="Author", value=track.author, inline=False)
                                await bot.text_channels[guild_id].send(embed=embed)
                            break  # Move to the next author after adding one track
            except Exception as e:
                print(f"Error fetching recommendation for {author}: {e}")
        
        # if guild_id in bot.text_channels:
        #     embed = nextcord.Embed(title="Recommendation Summary", color=nextcord.Color.blue())
        #     embed.add_field(name="Tracks Added", value=f"Added {recommended_tracks} recommended tracks to the queue.", inline=False)
        #     await bot.text_channels[guild_id].send(embed=embed)

# Add a function to manage recommendation history
def manage_recommendation_history(guild_id: int):
    if guild_id in bot.recommendation_history:
        while len(bot.recommendation_history[guild_id]) > bot.max_recommendation_history:
            bot.recommendation_history[guild_id].popleft()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
bot.run(TOKEN)