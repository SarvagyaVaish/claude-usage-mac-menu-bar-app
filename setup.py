from setuptools import setup

APP = ['app.py']
OPTIONS = {
    'argv_emulation': False,
    'plist': {
        'LSUIElement': True,           # hide dock icon — menu bar only
        'CFBundleName': 'ClaudeUsage',
        'CFBundleDisplayName': 'Claude Usage',
        'CFBundleIdentifier': 'com.local.claude-usage',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
    },
    'packages': ['rumps', 'requests'],
}

setup(
    app=APP,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
