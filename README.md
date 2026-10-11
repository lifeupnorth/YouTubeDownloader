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

## Disclaimer

This app is a personal tool and isn't affiliated with or endorsed by YouTube or Google. Downloading videos may go against [YouTube's Terms of Service](https://www.youtube.com/t/terms) and copyright law. You're responsible for only downloading content you have the right to, such as your own uploads or videos whose license allows it.

## License

MIT. See [LICENSE](LICENSE).
