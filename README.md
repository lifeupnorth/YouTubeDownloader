# YouTubeDownloader
A small app to download YouTube videos written in Python.

Install ffmpeg before running:
- Press WIN+R
- Type : Winget install ffmpeg
- Wait 10 seconds.
- Done.

The app checks for ffmpeg at startup and before each download. On Windows it
also finds an install made while the app is open.

Run the tests:
```
python -m unittest discover -s tests -v
```

## License

MIT. See [LICENSE](LICENSE).
