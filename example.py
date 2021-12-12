from mkv_reader import MKVReader, TRACK

# declare MKVReader that reads all tracks – color, depth, and IR
reader = MKVReader("./recording.mkv", track_filter=[TRACK.COLOR, TRACK.DEPTH, TRACK.IR])

# print file metadata, etc. if desired
print()
print("=== VIDEO METADATA ===")
print()
reader.print_file_info()
print()
calib = reader.get_calibration()
reader.print_calibration(pretty=True)

# Iterate over video
print()
print("=== VIDEO PLAYBACK ===")
while True:
	try:
		frameset = reader.get_next_frameset()
	except EOFError:
		break

	# 'frameset' is a dictionary object
	# In addition to 'index' and 'timestamp', track numbers, if available, are also keys
	# e.g., to get the infrared image of the frameset (KeyError is raised if a track doesn't exist in a frameset)
	ir_img = frameset[TRACK.IR]

	# Note: the color image may NOT be in some framesets, especially at the beginning and/or end of the MKV file
	# This happens, I suppose, when the Depth/IR sensor turns on/off a fraction of a second before/after the color sensor, but seems normal
	try:
		color_img = frameset[TRACK.COLOR]
	except KeyError:
		print(f"No color image in frameset #{frameset['index'] + 1}!")

	print(f"Frameset #{frameset['index'] + 1} (t = {frameset['timestamp']:06f} s)")
