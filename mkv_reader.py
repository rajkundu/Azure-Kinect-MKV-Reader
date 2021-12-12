# Licence==MIT; Vitaly "_Vi" Shukela 2012

# Simple easy-to-use hacky matroska parser

# Supports SimpleBlock and BlockGroup, lacing, TimecodeScale.
# Does not support seeking, cues, chapters and other features.
# No proper EOF handling unfortunately

from struct import unpack

import sys
import os
import datetime
import binascii
import json
import cv2
import numpy as np

class TRACK:
    COLOR = 1
    DEPTH = 2
    IR = 3
    IMU = 4

def ord(x):
    if type(x)==bytes:
        if len(x) == 0:
            raise StopIteration
        return x[0]
    else:
        return x

def get_major_bit_number(n):
    '''
        Takes uint8, returns number of the most significant bit plus the number with that bit cleared.
        Examples:
        0b10010101 -> (0, 0b00010101)
        0b00010101 -> (3, 0b00000101)
        0b01111111 -> (1, 0b00111111)
    '''
    if not n:
        raise Exception("Bad number")
    i=0x80
    r=0
    while not n&i:
        r+=1
        i>>=1
    return (r,n&~i)

def read_matroska_number(f, unmodified=False, signed=False):
    '''
        Read ebml number. Unmodified means don't clear the length bit (as in Element IDs)
        Returns the number and it's length as a tuple

        See examples in "parse_matroska_number" function
    '''
    if unmodified and signed:
        raise Exception("Contradictary arguments")
    first_byte=f.read(1)
    if(first_byte==""):
        raise StopIteration
    r = ord(first_byte)
    (n,r2) = get_major_bit_number(r)
    if not unmodified:
        r=r2
    # from now "signed" means "negative"
    i=n
    while i:
        r = r * 0x100 + ord(f.read(1))
        i-=1
    if signed:
        r-=(2**(7*n+7)-1)
    else:
        if r==2**(7*n+7)-1:
            return (-1, n+1)
    return (r,n+1)

def parse_matroska_number(data, pos, unmodified=False, signed=False):
    '''
        Parse ebml number from buffer[pos:]. Just like read_matroska_number.
        Unmodified means don't clear the length bit (as in Element IDs)
        Returns the number plus the new position in input buffer

        Examples:
        "\x81" -> (1, pos+1)
        "\x40\x01" -> (1, pos+2)
        "\x20\x00\x01" -> (1, pos+3)
        "\x3F\xFF\xFF" -> (0x1FFFFF, pos+3)
        "\x20\x00\x01" unmodified -> (0x200001, pos+3)
        "\xBF" signed -> (0, pos+1)
        "\xBE" signed -> (-1, pos+1)
        "\xC0" signed -> (1, pos+1)
        "\x5F\xEF" signed -> (-16, pos+2)
    '''
    if unmodified and signed:
        raise Exception("Contradictary arguments")
    r = ord(data[pos])
    pos+=1
    (n,r2) = get_major_bit_number(r)
    if not unmodified:
        r=r2
    # from now "signed" means "negative"
    i=n
    while i:
        r = r * 0x100 + ord(data[pos])
        pos+=1
        i-=1
    if signed:
        r-=(2**(7*n+6)-1)
    else:
        if r==2**(7*n+7)-1:
            return (-1, pos)
    return (r,pos)

def parse_xiph_number(data, pos):
    '''
        Parse the Xiph lacing number from data[pos:]
        Returns the number plus the new position

        Examples:
        "\x01" -> (1,    pos+1)
        "\x55" -> (0x55, pos+1)
        "\xFF\x04" -> (0x103,  pos+2)
        "\xFF\xFF\x04" -> (0x202,  pos+3)
        "\xFF\xFF\x00" -> (0x1FE,  pos+3)
    '''
    v = ord(data[pos])
    pos+=1

    r=0
    while v==255:
        r+=v
        v = ord(data[pos])
        pos+=1

    r+=v
    return (r, pos)


def parse_fixedlength_number(data, pos, length, signed=False):
    '''
        Read the big-endian number from data[pos:pos+length]
        Returns the number plus the new position

        Examples:
        "\x01" -> (0x1,    pos+1)
        "\x55" -> (0x55, pos+1)
        "\x55" signed -> (0x55, pos+1)
        "\xFF\x04" -> (0xFF04,  pos+2)
        "\xFF\x04" signed -> (-0x00FC,  pos+2)
    '''
    r=0
    for i in range(length):
        r=r*0x100+ord(data[pos+i])
    if signed:
        if ord(data[pos]) & 0x80:
            r-=2**(8*length)
    return (r, pos+length)

def read_fixedlength_number(f, length, signed=False):
    """ Read length bytes and parse (parse_fixedlength_number) it.
    Returns only the number"""
    buf = f.read(length)
    (r, pos) = parse_fixedlength_number(buf, 0, length, signed)
    return r
    
def read_ebml_element_header(f):
    '''
        Read Element ID and size
        Returns id, element size and this header size
    '''
    (id_, n) = read_matroska_number(f, unmodified=True)
    (size, n2) = read_matroska_number(f)
    return (id_, size, n+n2)

class EbmlElementType:
    VOID=0
    MASTER=1 # read all subelements and return tree. Don't use this too large things like Segment
    UNSIGNED=2
    SIGNED=3
    TEXTA=4
    TEXTU=5
    BINARY=6
    FLOAT=7
    DATE=8

    JUST_GO_ON=10 # For "Segment". 
    # Actually MASTER, but don't build the tree for all subelements, 
    # interpreting all child elements as if they were top-level elements
    

EET=EbmlElementType

element_types_names = {
    0x1A45DFA3: (EET.MASTER, "EBML"),
    0x4286: (EET.UNSIGNED, "EBMLVersion"),
    0x42F7: (EET.UNSIGNED, "EBMLReadVersion"),
    0x42F2: (EET.UNSIGNED, "EBMLMaxIDLength"),
    0x42F3: (EET.UNSIGNED, "EBMLMaxSizeLength"),
    0x4282: (EET.TEXTA, "DocType"),
    0x4287: (EET.UNSIGNED, "DocTypeVersion"),
    0x4285: (EET.UNSIGNED, "DocTypeReadVersion"),
    0xEC: (EET.BINARY, "Void"),
    0xBF: (EET.BINARY, "CRC-32"),
    0x1B538667: (EET.MASTER, "SignatureSlot"),
    0x7E8A: (EET.UNSIGNED, "SignatureAlgo"),
    0x7E9A: (EET.UNSIGNED, "SignatureHash"),
    0x7EA5: (EET.BINARY, "SignaturePublicKey"),
    0x7EB5: (EET.BINARY, "Signature"),
    0x7E5B: (EET.MASTER, "SignatureElements"),
    0x7E7B: (EET.MASTER, "SignatureElementList"),
    0x6532: (EET.BINARY, "SignedElement"),
    0x18538067: (EET.JUST_GO_ON, "Segment"),
    0x114D9B74: (EET.MASTER, "SeekHead"),
    0x4DBB: (EET.MASTER, "Seek"),
    0x53AB: (EET.BINARY, "SeekID"),
    0x53AC: (EET.UNSIGNED, "SeekPosition"),
    0x1549A966: (EET.MASTER, "Info"),
    0x73A4: (EET.BINARY, "SegmentUID"),
    0x7384: (EET.TEXTU, "SegmentFilename"),
    0x3CB923: (EET.BINARY, "PrevUID"),
    0x3C83AB: (EET.TEXTU, "PrevFilename"),
    0x3EB923: (EET.BINARY, "NextUID"),
    0x3E83BB: (EET.TEXTU, "NextFilename"),
    0x4444: (EET.BINARY, "SegmentFamily"),
    0x6924: (EET.MASTER, "ChapterTranslate"),
    0x69FC: (EET.UNSIGNED, "ChapterTranslateEditionUID"),
    0x69BF: (EET.UNSIGNED, "ChapterTranslateCodec"),
    0x69A5: (EET.BINARY, "ChapterTranslateID"),
    0x2AD7B1: (EET.UNSIGNED, "TimestampScale"),
    0x4489: (EET.FLOAT, "Duration"),
    0x4461: (EET.DATE, "DateUTC"),
    0x7BA9: (EET.TEXTU, "Title"),
    0x4D80: (EET.TEXTU, "MuxingApp"),
    0x5741: (EET.TEXTU, "WritingApp"),
    0x1F43B675: (EET.JUST_GO_ON, "Cluster"),
    0xE7: (EET.UNSIGNED, "Timestamp"),
    0x5854: (EET.MASTER, "SilentTracks"),
    0x58D7: (EET.UNSIGNED, "SilentTrackNumber"),
    0xA7: (EET.UNSIGNED, "Position"),
    0xAB: (EET.UNSIGNED, "PrevSize"),
    0xA3: (EET.BINARY, "SimpleBlock"),
    0xA0: (EET.MASTER, "BlockGroup"),
    0xA1: (EET.BINARY, "Block"),
    0xA2: (EET.BINARY, "BlockVirtual"),
    0x75A1: (EET.MASTER, "BlockAdditions"),
    0xA6: (EET.MASTER, "BlockMore"),
    0xEE: (EET.UNSIGNED, "BlockAddID"),
    0xA5: (EET.BINARY, "BlockAdditional"),
    0x9B: (EET.UNSIGNED, "BlockDuration"),
    0xFA: (EET.UNSIGNED, "ReferencePriority"),
    0xFB: (EET.SIGNED, "ReferenceBlock"),
    0xFD: (EET.SIGNED, "ReferenceVirtual"),
    0xA4: (EET.BINARY, "CodecState"),
    0x75A2: (EET.SIGNED, "DiscardPadding"),
    0x8E: (EET.MASTER, "Slices"),
    0xE8: (EET.MASTER, "TimeSlice"),
    0xCC: (EET.UNSIGNED, "LaceNumber"),
    0xCD: (EET.UNSIGNED, "FrameNumber"),
    0xCB: (EET.UNSIGNED, "BlockAdditionID"),
    0xCE: (EET.UNSIGNED, "Delay"),
    0xCF: (EET.UNSIGNED, "SliceDuration"),
    0xC8: (EET.MASTER, "ReferenceFrame"),
    0xC9: (EET.UNSIGNED, "ReferenceOffset"),
    0xCA: (EET.UNSIGNED, "ReferenceTimestamp"),
    0xAF: (EET.BINARY, "EncryptedBlock"),
    0x1654AE6B: (EET.MASTER, "Tracks"),
    0xAE: (EET.MASTER, "TrackEntry"),
    0xD7: (EET.UNSIGNED, "TrackNumber"),
    0x73C5: (EET.UNSIGNED, "TrackUID"),
    0x83: (EET.UNSIGNED, "TrackType"),
    0xB9: (EET.UNSIGNED, "FlagEnabled"),
    0x88: (EET.UNSIGNED, "FlagDefault"),
    0x55AA: (EET.UNSIGNED, "FlagForced"),
    0x9C: (EET.UNSIGNED, "FlagLacing"),
    0x6DE7: (EET.UNSIGNED, "MinCache"),
    0x6DF8: (EET.UNSIGNED, "MaxCache"),
    0x23E383: (EET.UNSIGNED, "DefaultDuration"),
    0x234E7A: (EET.UNSIGNED, "DefaultDecodedFieldDuration"),
    0x23314F: (EET.FLOAT, "TrackTimestampScale"),
    0x537F: (EET.SIGNED, "TrackOffset"),
    0x55EE: (EET.UNSIGNED, "MaxBlockAdditionID"),
    0x41E4: (EET.MASTER, "BlockAdditionMapping"),
    0x41F0: (EET.UNSIGNED, "BlockAddIDValue"),
    0x41A4: (EET.TEXTA, "BlockAddIDName"),
    0x41E7: (EET.UNSIGNED, "BlockAddIDType"),
    0x41ED: (EET.BINARY, "BlockAddIDExtraData"),
    0x536E: (EET.TEXTU, "Name"),
    0x22B59C: (EET.TEXTA, "Language"),
    0x22B59D: (EET.TEXTA, "LanguageIETF"),
    0x86: (EET.TEXTA, "CodecID"),
    0x63A2: (EET.BINARY, "CodecPrivate"),
    0x258688: (EET.TEXTU, "CodecName"),
    0x7446: (EET.UNSIGNED, "AttachmentLink"),
    0x3A9697: (EET.TEXTU, "CodecSettings"),
    0x3B4040: (EET.TEXTA, "CodecInfoURL"),
    0x26B240: (EET.TEXTA, "CodecDownloadURL"),
    0xAA: (EET.UNSIGNED, "CodecDecodeAll"),
    0x6FAB: (EET.UNSIGNED, "TrackOverlay"),
    0x56AA: (EET.UNSIGNED, "CodecDelay"),
    0x56BB: (EET.UNSIGNED, "SeekPreRoll"),
    0x6624: (EET.MASTER, "TrackTranslate"),
    0x66FC: (EET.UNSIGNED, "TrackTranslateEditionUID"),
    0x66BF: (EET.UNSIGNED, "TrackTranslateCodec"),
    0x66A5: (EET.BINARY, "TrackTranslateTrackID"),
    0xE0: (EET.MASTER, "Video"),
    0x9A: (EET.UNSIGNED, "FlagInterlaced"),
    0x9D: (EET.UNSIGNED, "FieldOrder"),
    0x53B8: (EET.UNSIGNED, "StereoMode"),
    0x53C0: (EET.UNSIGNED, "AlphaMode"),
    0x53B9: (EET.UNSIGNED, "OldStereoMode"),
    0xB0: (EET.UNSIGNED, "PixelWidth"),
    0xBA: (EET.UNSIGNED, "PixelHeight"),
    0x54AA: (EET.UNSIGNED, "PixelCropBottom"),
    0x54BB: (EET.UNSIGNED, "PixelCropTop"),
    0x54CC: (EET.UNSIGNED, "PixelCropLeft"),
    0x54DD: (EET.UNSIGNED, "PixelCropRight"),
    0x54B0: (EET.UNSIGNED, "DisplayWidth"),
    0x54BA: (EET.UNSIGNED, "DisplayHeight"),
    0x54B2: (EET.UNSIGNED, "DisplayUnit"),
    0x54B3: (EET.UNSIGNED, "AspectRatioType"),
    0x2EB524: (EET.BINARY, "ColourSpace"),
    0x2FB523: (EET.FLOAT, "GammaValue"),
    0x2383E3: (EET.FLOAT, "FrameRate"),
    0x55B0: (EET.MASTER, "Colour"),
    0x55B1: (EET.UNSIGNED, "MatrixCoefficients"),
    0x55B2: (EET.UNSIGNED, "BitsPerChannel"),
    0x55B3: (EET.UNSIGNED, "ChromaSubsamplingHorz"),
    0x55B4: (EET.UNSIGNED, "ChromaSubsamplingVert"),
    0x55B5: (EET.UNSIGNED, "CbSubsamplingHorz"),
    0x55B6: (EET.UNSIGNED, "CbSubsamplingVert"),
    0x55B7: (EET.UNSIGNED, "ChromaSitingHorz"),
    0x55B8: (EET.UNSIGNED, "ChromaSitingVert"),
    0x55B9: (EET.UNSIGNED, "Range"),
    0x55BA: (EET.UNSIGNED, "TransferCharacteristics"),
    0x55BB: (EET.UNSIGNED, "Primaries"),
    0x55BC: (EET.UNSIGNED, "MaxCLL"),
    0x55BD: (EET.UNSIGNED, "MaxFALL"),
    0x55D0: (EET.MASTER, "MasteringMetadata"),
    0x55D1: (EET.FLOAT, "PrimaryRChromaticityX"),
    0x55D2: (EET.FLOAT, "PrimaryRChromaticityY"),
    0x55D3: (EET.FLOAT, "PrimaryGChromaticityX"),
    0x55D4: (EET.FLOAT, "PrimaryGChromaticityY"),
    0x55D5: (EET.FLOAT, "PrimaryBChromaticityX"),
    0x55D6: (EET.FLOAT, "PrimaryBChromaticityY"),
    0x55D7: (EET.FLOAT, "WhitePointChromaticityX"),
    0x55D8: (EET.FLOAT, "WhitePointChromaticityY"),
    0x55D9: (EET.FLOAT, "LuminanceMax"),
    0x55DA: (EET.FLOAT, "LuminanceMin"),
    0x7670: (EET.MASTER, "Projection"),
    0x7671: (EET.UNSIGNED, "ProjectionType"),
    0x7672: (EET.BINARY, "ProjectionPrivate"),
    0x7673: (EET.FLOAT, "ProjectionPoseYaw"),
    0x7674: (EET.FLOAT, "ProjectionPosePitch"),
    0x7675: (EET.FLOAT, "ProjectionPoseRoll"),
    0xE1: (EET.MASTER, "Audio"),
    0xB5: (EET.FLOAT, "SamplingFrequency"),
    0x78B5: (EET.FLOAT, "OutputSamplingFrequency"),
    0x9F: (EET.UNSIGNED, "Channels"),
    0x7D7B: (EET.BINARY, "ChannelPositions"),
    0x6264: (EET.UNSIGNED, "BitDepth"),
    0xE2: (EET.MASTER, "TrackOperation"),
    0xE3: (EET.MASTER, "TrackCombinePlanes"),
    0xE4: (EET.MASTER, "TrackPlane"),
    0xE5: (EET.UNSIGNED, "TrackPlaneUID"),
    0xE6: (EET.UNSIGNED, "TrackPlaneType"),
    0xE9: (EET.MASTER, "TrackJoinBlocks"),
    0xED: (EET.UNSIGNED, "TrackJoinUID"),
    0xC0: (EET.UNSIGNED, "TrickTrackUID"),
    0xC1: (EET.BINARY, "TrickTrackSegmentUID"),
    0xC6: (EET.UNSIGNED, "TrickTrackFlag"),
    0xC7: (EET.UNSIGNED, "TrickMasterTrackUID"),
    0xC4: (EET.BINARY, "TrickMasterTrackSegmentUID"),
    0x6D80: (EET.MASTER, "ContentEncodings"),
    0x6240: (EET.MASTER, "ContentEncoding"),
    0x5031: (EET.UNSIGNED, "ContentEncodingOrder"),
    0x5032: (EET.UNSIGNED, "ContentEncodingScope"),
    0x5033: (EET.UNSIGNED, "ContentEncodingType"),
    0x5034: (EET.MASTER, "ContentCompression"),
    0x4254: (EET.UNSIGNED, "ContentCompAlgo"),
    0x4255: (EET.BINARY, "ContentCompSettings"),
    0x5035: (EET.MASTER, "ContentEncryption"),
    0x47E1: (EET.UNSIGNED, "ContentEncAlgo"),
    0x47E2: (EET.BINARY, "ContentEncKeyID"),
    0x47E7: (EET.MASTER, "ContentEncAESSettings"),
    0x47E8: (EET.UNSIGNED, "AESSettingsCipherMode"),
    0x47E3: (EET.BINARY, "ContentSignature"),
    0x47E4: (EET.BINARY, "ContentSigKeyID"),
    0x47E5: (EET.UNSIGNED, "ContentSigAlgo"),
    0x47E6: (EET.UNSIGNED, "ContentSigHashAlgo"),
    0x1C53BB6B: (EET.MASTER, "Cues"),
    0xBB: (EET.MASTER, "CuePoint"),
    0xB3: (EET.UNSIGNED, "CueTime"),
    0xB7: (EET.MASTER, "CueTrackPositions"),
    0xF7: (EET.UNSIGNED, "CueTrack"),
    0xF1: (EET.UNSIGNED, "CueClusterPosition"),
    0xF0: (EET.UNSIGNED, "CueRelativePosition"),
    0xB2: (EET.UNSIGNED, "CueDuration"),
    0x5378: (EET.UNSIGNED, "CueBlockNumber"),
    0xEA: (EET.UNSIGNED, "CueCodecState"),
    0xDB: (EET.MASTER, "CueReference"),
    0x96: (EET.UNSIGNED, "CueRefTime"),
    0x97: (EET.UNSIGNED, "CueRefCluster"),
    0x535F: (EET.UNSIGNED, "CueRefNumber"),
    0xEB: (EET.UNSIGNED, "CueRefCodecState"),
    0x1941A469: (EET.MASTER, "Attachments"),
    0x61A7: (EET.MASTER, "AttachedFile"),
    0x467E: (EET.TEXTU, "FileDescription"),
    0x466E: (EET.TEXTU, "FileName"),
    0x4660: (EET.TEXTA, "FileMimeType"),
    0x465C: (EET.BINARY, "FileData"),
    0x46AE: (EET.UNSIGNED, "FileUID"),
    0x4675: (EET.BINARY, "FileReferral"),
    0x4661: (EET.UNSIGNED, "FileUsedStartTime"),
    0x4662: (EET.UNSIGNED, "FileUsedEndTime"),
    0x1043A770: (EET.MASTER, "Chapters"),
    0x45B9: (EET.MASTER, "EditionEntry"),
    0x45BC: (EET.UNSIGNED, "EditionUID"),
    0x45BD: (EET.UNSIGNED, "EditionFlagHidden"),
    0x45DB: (EET.UNSIGNED, "EditionFlagDefault"),
    0x45DD: (EET.UNSIGNED, "EditionFlagOrdered"),
    0xB6: (EET.MASTER, "ChapterAtom"),
    0x73C4: (EET.UNSIGNED, "ChapterUID"),
    0x5654: (EET.TEXTU, "ChapterStringUID"),
    0x91: (EET.UNSIGNED, "ChapterTimeStart"),
    0x92: (EET.UNSIGNED, "ChapterTimeEnd"),
    0x98: (EET.UNSIGNED, "ChapterFlagHidden"),
    0x4598: (EET.UNSIGNED, "ChapterFlagEnabled"),
    0x6E67: (EET.BINARY, "ChapterSegmentUID"),
    0x6EBC: (EET.UNSIGNED, "ChapterSegmentEditionUID"),
    0x63C3: (EET.UNSIGNED, "ChapterPhysicalEquiv"),
    0x8F: (EET.MASTER, "ChapterTrack"),
    0x89: (EET.UNSIGNED, "ChapterTrackUID"),
    0x80: (EET.MASTER, "ChapterDisplay"),
    0x85: (EET.TEXTU, "ChapString"),
    0x437C: (EET.TEXTA, "ChapLanguage"),
    0x437D: (EET.TEXTA, "ChapLanguageIETF"),
    0x437E: (EET.TEXTA, "ChapCountry"),
    0x6944: (EET.MASTER, "ChapProcess"),
    0x6955: (EET.UNSIGNED, "ChapProcessCodecID"),
    0x450D: (EET.BINARY, "ChapProcessPrivate"),
    0x6911: (EET.MASTER, "ChapProcessCommand"),
    0x6922: (EET.UNSIGNED, "ChapProcessTime"),
    0x6933: (EET.BINARY, "ChapProcessData"),
    0x1254C367: (EET.MASTER, "Tags"),
    0x7373: (EET.MASTER, "Tag"),
    0x63C0: (EET.MASTER, "Targets"),
    0x68CA: (EET.UNSIGNED, "TargetTypeValue"),
    0x63CA: (EET.TEXTA, "TargetType"),
    0x63C5: (EET.UNSIGNED, "TagTrackUID"),
    0x63C9: (EET.UNSIGNED, "TagEditionUID"),
    0x63C4: (EET.UNSIGNED, "TagChapterUID"),
    0x63C6: (EET.UNSIGNED, "TagAttachmentUID"),
    0x67C8: (EET.MASTER, "SimpleTag"),
    0x45A3: (EET.TEXTU, "TagName"),
    0x447A: (EET.TEXTA, "TagLanguage"),
    0x447B: (EET.TEXTA, "TagLanguageIETF"),
    0x4484: (EET.UNSIGNED, "TagDefault"),
    0x4487: (EET.TEXTU, "TagString"),
    0x4485: (EET.BINARY, "TagBinary"),
}

def read_simple_element(f, type_, size):
    date = None
    if size==0:
        return ""

    if type_==EET.UNSIGNED:
        data=read_fixedlength_number(f, size, False)
    elif type_==EET.SIGNED:
        data=read_fixedlength_number(f, size, True)
    elif type_==EET.TEXTA:
        data=f.read(size)
        data = data.replace(b"\x00", b"")  # filter out \0, for gstreamer
        data = data.decode("ascii")
    elif type_==EET.TEXTU:
        data=f.read(size)
        data = data.replace(b"\x00", b"")  # filter out \0, for gstreamer
        data = data.decode("UTF-8")
    elif type_==EET.MASTER:
        data=read_ebml_element_tree(f, size)
    elif type_==EET.DATE:
        data=read_fixedlength_number(f, size, True)
        data*= 1e-9
        data+= (datetime.datetime(2001, 1, 1) - datetime.datetime(1970, 1, 1)).total_seconds()
        # now should be UNIX date
    elif type_==EET.FLOAT:
        if size==4:
            data = f.read(4)
            data = unpack(">f", data)[0]
        elif size==8:
            data = f.read(8)
            data = unpack(">d", data)[0]
        else:
            data=read_fixedlength_number(f, size, False)
            sys.stderr.write("mkvparse: Floating point of size %d is not supported\n" % size)
            data = None
    else:
        data=f.read(size)
    if not data:
        raise RuntimeError()
    return data

def read_ebml_element_tree(f, total_size):
    '''
        Build tree of elements, reading f until total_size reached
        Don't use for the whole segment, it's not Haskell

        Returns list of pairs (element_name, element_value).
        element_value can also be list of pairs
    '''
    childs=[]
    while(total_size>0):
        (id_, size, hsize) = read_ebml_element_header(f)
        if size == -1:
            sys.stderr.write("mkvparse: Element %x without size? Damaged data? Skipping %d bytes\n" % (id_, size, total_size))
            f.read(total_size)
            break
        if size>total_size:
            sys.stderr.write("mkvparse: Element %x with size %d? Damaged data? Skipping %d bytes\n" % (id_, size, total_size))
            f.read(total_size)
            break
        type_ = EET.BINARY
        name = "unknown_%x"%id_
        if id_ in element_types_names:
            (type_, name) = element_types_names[id_]
        data = read_simple_element(f, type_, size)
        total_size-=(size+hsize)
        childs.append((name, (type_, data))) 
    return childs
                

class MKVReader():
    timecode_scale = 1000000
    current_cluster_timecode = 0
    frameset_num = 0

    def __init__(self, filepath, track_filter=(), debug=False):
        self.filepath = os.path.realpath(filepath)
        self.filename = os.path.basename(self.filepath)
        self.file = open(self.filepath, "rb")
        self.debug = debug

        try:
            self.track_filter = set(track_filter)
        except TypeError:
            self.track_filter = set((track_filter,))
        
        # Note: reading IMU data from MKV is not currently implemented (but can VERY easily be implemented in several ways)
        # This is because IMU track data occurs several times – whereas image track data only occurs once (per track) – for each Matroska cluster.
        # Thus, the definition of "frameset" must be more explicitly defined if one wants to include IMU data.
        # Currently, a frameset is parsed from a single Matroska cluster.
        # Currently, an Azure MKV cluster either contains 0 (rare) or exactly 1 (common, barring frame drop) images per each type of track (color, depth, IR).
        # Please contact the repo maintainer and/or file an issue for more information!
        if TRACK.IMU in self.track_filter:
            raise ValueError("Reading Azure Kinect DK IMU data is currently not implemented!")

        self.read_metadata()
        
        if self.debug:
            self.print_file_info()
            self.print_metadata()
    
    def print_file_info(self, end=""):
        print(f"Filename: {os.path.basename(self.filepath)}")
        print(f"Filepath: {self.filepath}")
        print("Tracks:")
        for k in self.tracks:
            t=self.tracks[k]
            print(f"\t{k}\t{t['Name'][1]}\t{t['type'][1]}\t{t['CodecID'][1]}")
        if end:
            print(end)

    def print_metadata(self, end=""):
        print("Metadata:")
        for (k,(t_,v)) in self.file_metadata:
            if t_ == EbmlElementType.BINARY: v = binascii.hexlify(v)
            if t_ == EbmlElementType.DATE: v = str(datetime.datetime.utcfromtimestamp(v))
            print(f"\t{k}: {v}")
        if end:
            print(end)

    def read_metadata(self):
        while not self.file.closed:
            (id_, size, hsize) = (None, None, None)
            tree = None
            data = None
            (type_, name) = (None, None)
            try:
                (id_, size, hsize) = read_ebml_element_header(self.file)
                (type_, name) = element_types_names[id_]

                if type_ == EET.MASTER:
                    tree = read_ebml_element_tree(self.file, size)
                    data = tree

            except StopIteration:
                raise EOFError()
            
            if name=="Cluster":
                return
            
            if name=="Attachments":
                d = dict(tree)
                # seems like AttachedFile[0] is the # of files (for Azure Kinect DK, should be just 1 for calibration.json)
                for (k, v) in d['AttachedFile'][1]:
                    # v is a tuple of (#, data)... not sure what the # is, so ignoring it
                    _, data = v
                    if k == 'FileName' and data.lower() != "calibration.json": raise FileNotFoundError("calibration file not found")
                    if k == 'FileData': self.calibration_raw = json.loads(data)
                if not self.calibration_raw:
                    raise FileNotFoundError("calibration file data not found")
                self.calibration = self.calibration_raw['CalibrationInformation']
            
            if name=="EBML" and type(data) == list:
                d = dict(tree)
                if 'EBMLReadVersion' in d and d['EBMLReadVersion'][1]>1: sys.stderr.write("mkvparse: Warning: EBMLReadVersion too big\n")
                if 'DocTypeReadVersion' in d and d['DocTypeReadVersion'][1]>2: sys.stderr.write("mkvparse: Warning: DocTypeReadVersion too big\n")
                dt = d['DocType'][1]
                if dt not in ("matroska", "webm"): 
                    sys.stderr.write("mkvparse: Warning: EBML DocType is not \"matroska\" or \"webm\"")
            elif name=="Info" and type(data) == list:
                self.file_metadata = tree
                d = dict(tree)
                if "TimestampScale" in d:
                    self.timecode_scale = d["TimestampScale"][1] 
            elif name=="Tracks" and type(data) == list:
                self.tracks={}
                construct_track_filter = False
                if len(self.track_filter) == 0:
                    construct_track_filter = True
                for (ten, (_t, track)) in tree:
                    if ten != "TrackEntry": continue
                    d = dict(track)
                    n = d['TrackNumber'][1]
                    if construct_track_filter and n != TRACK.IMU: self.track_filter.add(n)
                    self.tracks[n]=d
                    tt = d['TrackType'][1]
                    if   tt==0x01: d['type']='video'
                    elif tt==0x02: d['type']='audio'
                    elif tt==0x03: d['type']='complex'
                    elif tt==0x10: d['type']='logo'
                    elif tt==0x11: d['type']='subtitle'
                    elif tt==0x12: d['type']='button'
                    elif tt==0x20: d['type']='control'
                    if 'TrackTimestampScale' in d:
                        sys.stderr.write("mkvparse: Warning: TrackTimestampScale is not supported\n")
                    if 'ContentEncodings' in d:
                        try:
                            compr = dict(d["ContentEncodings"][1][0][1][1][0][1][1])
                        except:
                            sys.stderr.write("mkvparse: Warning: unsuccessfully tried " \
                                    "to handle header removal compression\n")
            else:
                if type_!=EET.JUST_GO_ON and type_!=EET.MASTER:
                    data = read_simple_element(self.file, type_, size)

    def get_calibration(self):
        return self.calibration
    
    def print_calibration(self, pretty=True):
        print("Calibration:")
        if pretty:
            print(json.dumps(self.calibration, indent=2))
        else:
            print(self.calibration)
    
    def handle_frame(self, track_id, timestamp, frameset, data, more_laced_frames, duration, keyframe, invisible, discardable):
        if self.file.closed:
            raise EOFError()
        if track_id not in self.track_filter:
            return
        if track_id in frameset.keys():
            raise RuntimeError(f"Track {track_id} already in current frameset! Should only be one frame per track per frameset.")
        addstr = f"dur={duration:6f}" if duration else ""
        if keyframe: addstr+=" key"
        if invisible: addstr+=" invis"
        if discardable: addstr+=" disc"
        if self.debug: print(f"Frame for {track_id} ts={timestamp:06f} l={more_laced_frames} {addstr} len={len(data)} data={binascii.hexlify(data[0:10])}...")
        if track_id == TRACK.COLOR:
            data = cv2.imdecode(np.frombuffer(data, np.uint8), -1)
        elif track_id in (TRACK.DEPTH, TRACK.IR):
            arr = np.frombuffer(data, dtype=">i2")
            # NFOV unbinned
            if(arr.shape[0] == (640*576)):
                data = arr.reshape(576, 640)
            # NFOV 2x2 binned (SW)
            elif(arr.shape[0] == (320*288)):
                data = arr.reshape(288, 320)
            # WFOV 2x2 binned
            elif(arr.shape[0] == (512*512)):
                data = arr.reshape(512, 512)
            # WFOV unbinned, Passive IR
            elif(arr.shape[0] == (1024*1024)):
                data = arr.reshape(1024, 1024)
            else:
                print(f"ERROR: Received Depth/IR image in unknown format! Shape = {arr.shape}")
        
        if data is not None:
            frameset[track_id] = data
    
    def handle_block(self, buffer, cluster_timecode, frameset, timecode_scale=1000000, duration=None):
        '''
        Decode a block, handling all lacings
        '''
        pos=0
        (tracknum, pos) = parse_matroska_number(buffer, pos, signed=False)
        (tcode, pos) = parse_fixedlength_number(buffer, pos, 2, signed=True)
        flags = ord(buffer[pos]); pos+=1
        f_keyframe = (flags&0x80 == 0x80)
        f_invisible = (flags&0x08 == 0x08)
        f_discardable = (flags&0x01 == 0x01)
        laceflags=flags&0x06

        block_timecode = (cluster_timecode + tcode)*(timecode_scale*0.000000001)

        frameset['index'] = self.frameset_num
        frameset['timestamp'] = block_timecode

        if laceflags == 0x00: # no lacing
            buf = buffer[pos:]
            return self.handle_frame(tracknum, block_timecode, frameset, buf, 0, duration, f_keyframe, f_invisible, f_discardable)
        
        if tracknum not in self.track_filter:
            return
        
        numframes = ord(buffer[pos]); pos+=1
        numframes+=1

        lengths=[]

        if laceflags == 0x02: # Xiph lacing
            accumlength=0
            for i in range(numframes-1):
                (l, pos) = parse_xiph_number(buffer, pos)
                lengths.append(l)
                accumlength+=l
            lengths.append(len(buffer)-pos-accumlength)
        elif laceflags == 0x06: # EBML lacing
            accumlength=0
            if numframes:
                (flength, pos) = parse_matroska_number(buffer, pos, signed=False)
                lengths.append(flength)
                accumlength+=flength
            for i in range(numframes-2):
                (l, pos) = parse_matroska_number(buffer, pos, signed=True)
                flength+=l
                lengths.append(flength)
                accumlength+=flength
            lengths.append(len(buffer)-pos-accumlength)
        elif laceflags==0x04: # Fixed size lacing
            fl=int((len(buffer)-pos)/numframes)
            for i in range(numframes):
                lengths.append(fl)

        more_laced_frames=numframes-1
        for i in lengths:
            buf = buffer[pos:pos+i]
            pos+=i
            self.handle_frame(tracknum, block_timecode, frameset, buf, more_laced_frames, duration, f_keyframe, f_invisible, f_discardable)
            more_laced_frames-=1
        
    def get_next_frameset(self):
        next_byte = self.file.read(1)
        if not next_byte:
            if not self.file.closed:
                self.file.close()
            else:
                raise EOFError(f"Reached end of file '{self.filename}'")
        else:
            self.file.seek(-1, 1)
        
        frameset = {}
        while not self.file.closed:
            (id_, size, hsize) = (None, None, None)
            tree = None
            data = None
            (type_, name) = (None, None)
            try:
                (id_, size, hsize) = read_ebml_element_header(self.file)
                (type_, name) = element_types_names[id_]
                if type_ == EET.MASTER:
                    tree = read_ebml_element_tree(self.file, size)
                    data = tree
            except StopIteration:
                self.file.close()
                raise EOFError(f"Reached end of file '{self.filename}'")
            
            if name in ("EBML", "Info", "Tracks") and type(data) == list:
                raise RuntimeError("The read_metadata() function must be called exactly once before retrieving framesets.")

            if name=="Cluster":
                if len(set(frameset.keys()).intersection(self.track_filter)) == 0:
                    continue
                self.frameset_num += 1
                return frameset

            # cluster contents:
            elif name=="Timestamp" and type_ == EET.UNSIGNED:
                data=read_fixedlength_number(self.file, size, False)
                self.current_cluster_timecode = data
            elif name=="SimpleBlock" and type_ == EET.BINARY:
                data=self.file.read(size)
                self.handle_block(data, self.current_cluster_timecode, frameset, self.timecode_scale, None)
            elif name=="BlockGroup" and type_ == EET.MASTER:
                d2 = dict(tree)
                duration=None
                if 'BlockDuration' in d2:
                    duration = d2['BlockDuration'][1]
                    duration = duration*0.000000001*self.timecode_scale
                if 'Block' in d2:
                    self.handle_block(d2['Block'][1], self.current_cluster_timecode, frameset, self.timecode_scale, duration)
            else:
                if type_!=EET.JUST_GO_ON and type_!=EET.MASTER:
                    data = read_simple_element(self.file, type_, size)
