# 🎵 Music-Player

A terminal-based music player and recommendation engine with YouTube integration.

## 📋 Overview

Music-Player is a sophisticated Python application that provides music playback capabilities with intelligent recommendation features. Stream YouTube music directly from your terminal with smart suggestions!

## 🚀 Getting Started

### Prerequisites
- Python 3.6 or higher
- YouTube access
- Internet connection
- Audio playback device/speakers
- FFmpeg (for audio conversion)

### Installation

```bash
git clone https://github.com/PlayerRS21/Music-Player.git
cd Music-Player
pip install -r requirements.txt
python setup.py install
```

### Usage

```bash
python YtTerm.py
```

## 🎵 Features

- 🎬 YouTube music streaming
- 🎧 Terminal-based player interface
- 🤖 Smart recommendations based on history
- 📋 Playlist management and creation
- 🔍 Music search functionality
- 📱 Playback history tracking
- ⭐ Favorite songs management
- 🎛️ Volume and playback controls
- ⏭️ Skip, pause, resume functionality
- 📊 Statistics and trending tracks

## 🎨 Components

### YtTerm.py
Main terminal interface for the music player. Handles user input, playback controls, and display.

### recommender.py
ML-based recommendation engine that suggests music based on:
- Your listening history
- Music genres and styles you prefer
- User preferences and ratings
- Trending tracks
- Similar artist recommendations

### setup.py
Installation and configuration script for easy setup and dependency management.

## 🤖 Recommendation System

The built-in recommender provides personalized suggestions based on:

- **Listening History** - Analyzes songs you've played
- **Genre Preferences** - Learns your favorite genres
- **User Ratings** - Considers songs you've rated
- **Trending Data** - Includes popular current tracks
- **Similar Artists** - Suggests related music
- **Mood Analysis** - Recommends based on listening patterns

## ⚙️ Configuration

Customize settings in the config file:

```python
# Audio quality options
AUDIO_QUALITY = 'high'  # high, medium, low

# Default playlists
DEFAULT_PLAYLISTS = ['Favorites', 'Recently Played']

# Recommendation sensitivity
RECOMMENDATION_STRENGTH = 0.7

# User preferences
AUTO_PLAY_NEXT = True
SHUFFLE_MODE = False
REPEAT_MODE = 'off'  # off, one, all
```

## 🎹 Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Space` | Play/Pause |
| `→` | Next Track |
| `←` | Previous Track |
| `+` | Volume Up |
| `-` | Volume Down |
| `s` | Search |
| `p` | Add to Playlist |
| `f` | Add to Favorites |
| `h` | Show History |
| `q` | Quit |

## 📦 Requirements

See `requirements.txt` for detailed dependencies. Key packages include:

- **yt-dlp** - YouTube downloading and streaming
- **pygame** or **python-vlc** - Audio playback
- **numpy** - Recommendation calculations
- **requests** - API requests
- **colorama** - Terminal colors

## 🖥️ User Interface

- Clean terminal UI with status display
- Keyboard shortcuts for quick navigation
- Real-time playback information
- Search and browse interface
- Playlist management screen
- Recommendation display
- Track queue visualization

## 📊 Features in Detail

### Playlist Management
- Create custom playlists
- Add/remove songs from playlists
- Reorder playlist items
- Export playlists
- Import playlists from files

### Search Capabilities
- Search by song name
- Search by artist
- Search by album
- Search by genre
- Advanced filters

### History & Statistics
- Track play count
- Most played songs
- Recently played tracks
- Listening time statistics
- Genre breakdown

## 🔧 Troubleshooting

**No audio output?**
- Check FFmpeg installation
- Verify audio device is connected
- Test system audio: `speaker-test -t sine -f 1000 -l 5`

**YouTube videos not found?**
- Update yt-dlp: `pip install --upgrade yt-dlp`
- Check internet connection
- Verify YouTube is accessible in your region

**Recommendations not working?**
- Build listening history by playing more songs
- Check dataset in recommendation engine
- Restart player to refresh cache

## 📝 License

This project is currently unlicensed.

## 👤 Author

**PlayerRS21** - [GitHub Profile](https://github.com/PlayerRS21)

---

**Last Updated**: 2026