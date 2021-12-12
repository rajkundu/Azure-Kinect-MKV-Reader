# AzureKinectMKVReader

## A class for reading Azure Kinect DK MKV files in Python 3
### Benefits:
- Runs nearly universally – reads file byte-by-byte, **_does NOT_** depend on the Azure Kinect SDK
### Limitations:
- No "seek"/"skip" support
  - I think this could be implemented? I just don't need it, so I didn't implement it. Please contact me or file an issue to discuss!
- No current IMU data support...
  - This is actually very easy to implement but was removed for convenience of defining a "frameset": Azure Kinect DK image data occurs only once per Matroska cluster, but IMU data occurs several times, so it was decided to just ignore IMU data for now to simplify things. However, the data is there and readable. Please contact me or file an issue if you'd like discuss adding support for reading IMU data!

## Usage
```python
from mkv_reader import MKVReader, TRACK

# Initialize MKVReader object
reader = MKVReader("./recording.mkv")
calib = reader.get_calibration()

while True:
  try:
    frameset = reader.get_next_frameset()
  except EOFError:
    break

  # Use frameset...
  color_img = frameset[TRACK.COLOR]
```
Please see [example.py](example.py) for a more detailed example!

## Contributions
Any feedback and/or contributions are extremely welcome! :)

## License
License = MIT
