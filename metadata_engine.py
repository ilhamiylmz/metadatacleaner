#!/usr/bin/env python3
"""
metadata_engine.py - Pure-Python lossless image metadata & AI provenance engine.

Supports reading, editing, and stripping metadata from JPEG and PNG images:
- AI & Content Provenance:
  - C2PA / JUMBF Content Credentials (JPEG APP11 0xFFEB, PNG caBX)
  - CBOR claims, actions (e.g., Google Generative AI, SynthID watermark),
    ingredients, data hashes, and COSE_Sign1 X.509 certificate chains & timestamps
  - AI generation parameters in EXIF / XMP / PNG text / JPEG comments
    (Stable Diffusion, ComfyUI, Midjourney, DALL-E, Firefly, Google AI / SynthID)
  - Social media tracking tags (Facebook/Instagram IPTC Special Instructions FBMD)
- Standard Metadata:
  - EXIF (TIFF IFD0, ExifIFD, GPSIFD, InteropIFD, IFD1)
  - IPTC-NAA & Photoshop 8BIM (JPEG APP13 0xFFED)
  - XMP RDF/XML (JPEG APP1 0xFFE1, PNG iTXt)
  - JFIF (JPEG APP0 0xFFE0), ICC Profile (APP2), Adobe (APP14), Comments (COM 0xFFFE)
  - PNG text chunks (tEXt, zTXt, iTXt, eXIf, caBX, pHYs, tIME)
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from cryptography import x509
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


# ============================================================================
# Constants & Tag Dictionaries
# ============================================================================

JPEG_MARKER_NAMES: Dict[int, str] = {
    0xD8: "SOI (Start of Image)",
    0xD9: "EOI (End of Image)",
    0xDA: "SOS (Start of Scan)",
    0xDB: "DQT (Define Quantization Table)",
    0xDC: "DNL (Define Number of Lines)",
    0xDD: "DRI (Define Restart Interval)",
    0xC0: "SOF0 (Baseline DCT)",
    0xC1: "SOF1 (Extended Sequential DCT)",
    0xC2: "SOF2 (Progressive DCT)",
    0xC3: "SOF3 (Lossless)",
    0xC4: "DHT (Define Huffman Table)",
    0xE0: "APP0 (JFIF / JFXX)",
    0xE1: "APP1 (EXIF / XMP)",
    0xE2: "APP2 (ICC Profile / MPF)",
    0xE3: "APP3 (Meta)",
    0xE4: "APP4",
    0xE5: "APP5",
    0xE6: "APP6",
    0xE7: "APP7",
    0xE8: "APP8",
    0xE9: "APP9",
    0xEA: "APP10",
    0xEB: "APP11 (JPEG XT / JUMBF / C2PA)",
    0xEC: "APP12 (Picture Info / Ducky)",
    0xED: "APP13 (Photoshop 8BIM / IPTC)",
    0xEE: "APP14 (Adobe)",
    0xEF: "APP15",
    0xFE: "COM (Comment)",
}

EXIF_TAGS: Dict[int, str] = {
    0x0100: "ImageWidth",
    0x0101: "ImageLength",
    0x0102: "BitsPerSample",
    0x0103: "Compression",
    0x0106: "PhotometricInterpretation",
    0x010E: "ImageDescription",
    0x010F: "Make",
    0x0110: "Model",
    0x0112: "Orientation",
    0x0115: "SamplesPerPixel",
    0x011A: "XResolution",
    0x011B: "YResolution",
    0x011C: "PlanarConfiguration",
    0x0128: "ResolutionUnit",
    0x0131: "Software",
    0x0132: "DateTime",
    0x013B: "Artist",
    0x013E: "WhitePoint",
    0x013F: "PrimaryChromaticities",
    0x0201: "JPEGInterchangeFormat",
    0x0202: "JPEGInterchangeFormatLength",
    0x0213: "YCbCrPositioning",
    0x8298: "Copyright",
    0x829A: "ExposureTime",
    0x829D: "FNumber",
    0x8769: "ExifOffset",
    0x8822: "ExposureProgram",
    0x8825: "GPSInfo",
    0x8827: "ISOSpeedRatings",
    0x9000: "ExifVersion",
    0x9003: "DateTimeOriginal",
    0x9004: "DateTimeDigitized",
    0x9010: "OffsetTime",
    0x9011: "OffsetTimeOriginal",
    0x9012: "OffsetTimeDigitized",
    0x9101: "ComponentsConfiguration",
    0x9102: "CompressedBitsPerPixel",
    0x9201: "ShutterSpeedValue",
    0x9202: "ApertureValue",
    0x9203: "BrightnessValue",
    0x9204: "ExposureBiasValue",
    0x9205: "MaxApertureValue",
    0x9206: "SubjectDistance",
    0x9207: "MeteringMode",
    0x9208: "LightSource",
    0x9209: "Flash",
    0x920A: "FocalLength",
    0x927C: "MakerNote",
    0x9286: "UserComment",
    0x9290: "SubsecTime",
    0x9291: "SubsecTimeOriginal",
    0x9292: "SubsecTimeDigitized",
    0x9C9B: "XPTitle",
    0x9C9C: "XPComment",
    0x9C9D: "XPAuthor",
    0x9C9E: "XPKeywords",
    0x9C9F: "XPSubject",
    0xA000: "FlashPixVersion",
    0xA001: "ColorSpace",
    0xA002: "ExifImageWidth",
    0xA003: "ExifImageHeight",
    0xA004: "RelatedSoundFile",
    0xA005: "InteropOffset",
    0xA20E: "FocalPlaneXResolution",
    0xA20F: "FocalPlaneYResolution",
    0xA210: "FocalPlaneResolutionUnit",
    0xA217: "SensingMethod",
    0xA300: "FileSource",
    0xA301: "SceneType",
    0xA401: "CustomRendered",
    0xA402: "ExposureMode",
    0xA403: "WhiteBalance",
    0xA404: "DigitalZoomRatio",
    0xA405: "FocalLengthIn35mmFilm",
    0xA406: "SceneCaptureType",
    0xA408: "Contrast",
    0xA409: "Saturation",
    0xA40A: "Sharpness",
    0xA420: "ImageUniqueID",
    0xA430: "CameraOwnerName",
    0xA431: "BodySerialNumber",
    0xA432: "LensSpecification",
    0xA433: "LensMake",
    0xA434: "LensModel",
    0xA435: "LensSerialNumber",
}

EXIF_NAME_TO_TAG: Dict[str, int] = {v: k for k, v in EXIF_TAGS.items()}

# Standard editable EXIF tags and where they belong (IFD0 vs ExifIFD)
EDITABLE_EXIF_IFD0_TAGS: Dict[str, Tuple[int, int]] = {
    "ImageDescription": (0x010E, 2),  # ASCII
    "Make": (0x010F, 2),              # ASCII
    "Model": (0x0110, 2),             # ASCII
    "Software": (0x0131, 2),          # ASCII
    "DateTime": (0x0132, 2),          # ASCII
    "Artist": (0x013B, 2),            # ASCII
    "Copyright": (0x8298, 2),         # ASCII
}

EDITABLE_EXIF_SUB_TAGS: Dict[str, Tuple[int, int]] = {
    "DateTimeOriginal": (0x9003, 2),  # ASCII
    "DateTimeDigitized": (0x9004, 2), # ASCII
    "UserComment": (0x9286, 7),       # UNDEFINED (8-byte charset prefix + text)
    "ImageUniqueID": (0xA420, 2),     # ASCII
    "CameraOwnerName": (0xA430, 2),   # ASCII
    "LensMake": (0xA433, 2),          # ASCII
    "LensModel": (0xA434, 2),         # ASCII
}

GPS_TAGS: Dict[int, str] = {
    0x0000: "GPSVersionID",
    0x0001: "GPSLatitudeRef",
    0x0002: "GPSLatitude",
    0x0003: "GPSLongitudeRef",
    0x0004: "GPSLongitude",
    0x0005: "GPSAltitudeRef",
    0x0006: "GPSAltitude",
    0x0007: "GPSTimeStamp",
    0x0012: "GPSMapDatum",
    0x001D: "GPSDateStamp",
}

IPTC_RECORD2_TAGS: Dict[int, str] = {
    5: "ObjectName",
    7: "EditStatus",
    10: "Urgency",
    15: "Category",
    20: "SupplementalCategories",
    25: "Keywords",
    40: "SpecialInstructions",
    55: "DateCreated",
    60: "TimeCreated",
    62: "DigitalCreationDate",
    63: "DigitalCreationTime",
    80: "Byline",
    85: "BylineTitle",
    90: "City",
    92: "Sublocation",
    95: "ProvinceState",
    100: "CountryPrimaryLocationCode",
    101: "CountryPrimaryLocationName",
    103: "OriginalTransmissionReference",
    105: "Headline",
    110: "Credit",
    115: "Source",
    116: "CopyrightNotice",
    118: "Contact",
    120: "CaptionAbstract",
    122: "WriterEditor",
    135: "DigitalSourceType",
}

IPTC_NAME_TO_DS: Dict[str, int] = {v: k for k, v in IPTC_RECORD2_TAGS.items()}

PHOTOSHOP_8BIM_NAMES: Dict[int, str] = {
    0x0404: "IPTC-NAA Record",
    0x0408: "Grid and Guides",
    0x040A: "Copyright Flag",
    0x040C: "Thumbnail Resource",
    0x040D: "Global Angle",
    0x0414: "Document-Specific IDs Seed",
    0x0419: "Global Altitude",
    0x041A: "Slices",
    0x041E: "URL List",
    0x0421: "Version Info",
    0x0422: "Exif Data 1",
    0x0424: "XMP Metadata",
    0x0425: "Caption Digest (MD5)",
    0x0426: "Print Scale",
    0x0428: "Pixel Aspect Ratio",
    0x043A: "Print Info",
    0x043B: "Print Style",
}

DIGITAL_SOURCE_TYPE_LABELS: Dict[str, str] = {
    "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia":
        "Trained Algorithmic Media (AI-Generated)",
    "https://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia":
        "Trained Algorithmic Media (AI-Generated)",
    "trainedAlgorithmicMedia":
        "Trained Algorithmic Media (AI-Generated)",
    "http://cv.iptc.org/newscodes/digitalsourcetype/compositeWithTrainedAlgorithmicMedia":
        "Composite with Trained Algorithmic Media (AI-Edited / Inpainted)",
    "https://cv.iptc.org/newscodes/digitalsourcetype/compositeWithTrainedAlgorithmicMedia":
        "Composite with Trained Algorithmic Media (AI-Edited / Inpainted)",
    "compositeWithTrainedAlgorithmicMedia":
        "Composite with Trained Algorithmic Media (AI-Edited / Inpainted)",
    "http://cv.iptc.org/newscodes/digitalsourcetype/algorithmicMedia":
        "Pure Algorithmic Media",
    "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture":
        "Digital Capture (Camera/Sensor)",
    "http://cv.iptc.org/newscodes/digitalsourcetype/digitalArt":
        "Digital Art (Human Created)",
}

COSE_ALGORITHMS: Dict[int, str] = {
    -7: "ES256 (ECDSA w/ SHA-256, P-256)",
    -35: "ES384 (ECDSA w/ SHA-384, P-384)",
    -36: "ES512 (ECDSA w/ SHA-512, P-521)",
    -37: "PS256 (RSASSA-PSS w/ SHA-256)",
    -38: "PS384 (RSASSA-PSS w/ SHA-384)",
    -39: "PS512 (RSASSA-PSS w/ SHA-512)",
    -8: "EdDSA",
}


# ============================================================================
# CBOR Codec (Pure Python Decoder & Encoder for C2PA / COSE)
# ============================================================================

@dataclass
class CBORTag:
    tag: int
    value: Any


def decode_cbor(data: bytes, offset: int = 0) -> Tuple[Any, int]:
    """Decode a single CBOR item from `data` starting at `offset`."""
    if offset >= len(data):
        raise ValueError("Unexpected end of CBOR data")
    ib = data[offset]
    offset += 1
    major = ib >> 5
    addl = ib & 0x1F

    if addl < 24:
        val = addl
    elif addl == 24:
        val = data[offset]
        offset += 1
    elif addl == 25:
        val = struct.unpack(">H", data[offset : offset + 2])[0]
        offset += 2
    elif addl == 26:
        val = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
    elif addl == 27:
        val = struct.unpack(">Q", data[offset : offset + 8])[0]
        offset += 8
    elif addl == 31:
        # Indefinite length container
        if major == 2:
            chunks = []
            while offset < len(data) and data[offset] != 0xFF:
                c, offset = decode_cbor(data, offset)
                chunks.append(c)
            return b"".join(chunks), offset + 1
        elif major == 3:
            chunks = []
            while offset < len(data) and data[offset] != 0xFF:
                c, offset = decode_cbor(data, offset)
                chunks.append(c)
            return "".join(chunks), offset + 1
        elif major == 4:
            arr = []
            while offset < len(data) and data[offset] != 0xFF:
                item, offset = decode_cbor(data, offset)
                arr.append(item)
            return arr, offset + 1
        elif major == 5:
            mp = {}
            while offset < len(data) and data[offset] != 0xFF:
                k, offset = decode_cbor(data, offset)
                v, offset = decode_cbor(data, offset)
                mp[k] = v
            return mp, offset + 1
        return None, offset + 1
    else:
        raise ValueError(f"Unsupported CBOR additional info: {addl}")

    if major == 0:
        return val, offset
    elif major == 1:
        return -1 - val, offset
    elif major == 2:
        raw = data[offset : offset + val]
        offset += val
        return raw, offset
    elif major == 3:
        raw = data[offset : offset + val]
        offset += val
        return raw.decode("utf-8", errors="replace"), offset
    elif major == 4:
        arr = []
        for _ in range(val):
            item, offset = decode_cbor(data, offset)
            arr.append(item)
        return arr, offset
    elif major == 5:
        mp = {}
        for _ in range(val):
            k, offset = decode_cbor(data, offset)
            v, offset = decode_cbor(data, offset)
            mp[k] = v
        return mp, offset
    elif major == 6:
        item, offset = decode_cbor(data, offset)
        return CBORTag(val, item), offset
    elif major == 7:
        if addl == 20:
            return False, offset
        if addl == 21:
            return True, offset
        if addl in (22, 23):
            return None, offset
        if addl == 26:
            return struct.unpack(">f", struct.pack(">I", val))[0], offset
        if addl == 27:
            return struct.unpack(">d", struct.pack(">Q", val))[0], offset
        return val, offset
    return None, offset


def _encode_cbor_head(major: int, val: int) -> bytes:
    prefix = major << 5
    if val < 24:
        return bytes([prefix | val])
    elif val <= 0xFF:
        return bytes([prefix | 24, val])
    elif val <= 0xFFFF:
        return bytes([prefix | 25]) + struct.pack(">H", val)
    elif val <= 0xFFFFFFFF:
        return bytes([prefix | 26]) + struct.pack(">I", val)
    else:
        return bytes([prefix | 27]) + struct.pack(">Q", val)


def encode_cbor(obj: Any) -> bytes:
    """Encode a Python value back into CBOR bytes."""
    if isinstance(obj, CBORTag):
        return _encode_cbor_head(6, obj.tag) + encode_cbor(obj.value)
    elif obj is None:
        return bytes([0xF6])
    elif obj is False:
        return bytes([0xF4])
    elif obj is True:
        return bytes([0xF5])
    elif isinstance(obj, int):
        if obj >= 0:
            return _encode_cbor_head(0, obj)
        else:
            return _encode_cbor_head(1, -1 - obj)
    elif isinstance(obj, float):
        return bytes([0xFB]) + struct.pack(">d", obj)
    elif isinstance(obj, bytes):
        return _encode_cbor_head(2, len(obj)) + obj
    elif isinstance(obj, str):
        raw = obj.encode("utf-8")
        return _encode_cbor_head(3, len(raw)) + raw
    elif isinstance(obj, (list, tuple)):
        out = [_encode_cbor_head(4, len(obj))]
        for item in obj:
            out.append(encode_cbor(item))
        return b"".join(out)
    elif isinstance(obj, dict):
        out = [_encode_cbor_head(5, len(obj))]
        for k, v in obj.items():
            out.append(encode_cbor(k))
            out.append(encode_cbor(v))
        return b"".join(out)
    else:
        raw = str(obj).encode("utf-8")
        return _encode_cbor_head(3, len(raw)) + raw


def cbor_to_jsonable(obj: Any) -> Any:
    """Convert decoded CBOR structure (with raw bytes/CBORTags) into JSON-friendly objects."""
    if isinstance(obj, CBORTag):
        return {"_cbor_tag": obj.tag, "value": cbor_to_jsonable(obj.value)}
    elif isinstance(obj, bytes):
        if len(obj) <= 32:
            return f"0x{obj.hex()}"
        return f"<bytes len={len(obj)} sha256={hashlib.sha256(obj).hexdigest()[:16]}...>"
    elif isinstance(obj, list):
        return [cbor_to_jsonable(x) for x in obj]
    elif isinstance(obj, dict):
        return {str(k): cbor_to_jsonable(v) for k, v in obj.items()}
    return obj


# ============================================================================
# JUMBF & C2PA Parser / Builder
# ============================================================================

def parse_jumbf_boxes(data: bytes, start: int = 0, end: Optional[int] = None) -> List[Dict[str, Any]]:
    """Recursively parse ISO/IEC 19566-5 JUMBF boxes and embedded CBOR/JSON/UUID payloads."""
    if end is None:
        end = len(data)
    boxes: List[Dict[str, Any]] = []
    pos = start
    while pos + 8 <= end:
        lbox, tbox = struct.unpack(">I4s", data[pos : pos + 8])
        hdr_len = 8
        if lbox == 1:
            if pos + 16 > end:
                break
            lbox = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
            hdr_len = 16
        elif lbox == 0:
            lbox = end - pos
        if lbox < hdr_len or pos + lbox > end:
            break

        payload = data[pos + hdr_len : pos + lbox]
        tbox_str = tbox.decode("latin1", errors="replace")
        box_info: Dict[str, Any] = {
            "type": tbox_str,
            "length": lbox,
            "raw_payload": payload,
        }

        if tbox == b"jumb":
            children = parse_jumbf_boxes(payload, 0, len(payload))
            box_info["children"] = children
            for c in children:
                if c["type"] == "jumd":
                    box_info["label"] = c.get("label", "")
                    box_info["uuid_type"] = c.get("uuid_type", "")
                    box_info["uuid_hex"] = c.get("uuid_hex", "")
        elif tbox == b"jumd":
            uuid_bytes = payload[:16]
            toggles = payload[16] if len(payload) > 16 else 0
            box_info["uuid_bytes"] = uuid_bytes
            box_info["uuid_hex"] = uuid_bytes.hex()
            box_info["uuid_type"] = uuid_bytes[:4].decode("latin1", errors="replace")
            box_info["toggles"] = toggles
            p = 17
            if toggles & 0x02:
                nul = payload.find(b"\x00", p)
                if nul != -1:
                    box_info["label"] = payload[p:nul].decode("utf-8", errors="replace")
                    p = nul + 1
            if toggles & 0x04 and p + 4 <= len(payload):
                box_info["id"] = struct.unpack(">I", payload[p : p + 4])[0]
                p += 4
            if toggles & 0x08 and p + 32 <= len(payload):
                box_info["jumd_signature"] = payload[p : p + 32].hex()
                p += 32
            box_info["jumd_trailing"] = payload[p:]
        elif tbox == b"cbor":
            try:
                decoded, _ = decode_cbor(payload, 0)
                box_info["cbor_raw"] = decoded
                box_info["cbor"] = cbor_to_jsonable(decoded)
            except Exception as exc:
                box_info["cbor_error"] = str(exc)
        elif tbox == b"json":
            try:
                box_info["json"] = json.loads(payload.decode("utf-8", errors="replace"))
            except Exception as exc:
                box_info["json_error"] = str(exc)
        elif tbox == b"uuid":
            box_info["uuid_hex"] = payload[:16].hex()
            box_info["uuid_prefix"] = payload[:4].decode("latin1", errors="replace")
            box_info["uuid_data"] = payload[16:]

        boxes.append(box_info)
        pos += lbox
    return boxes


def serialize_jumbf_boxes(boxes: List[Dict[str, Any]]) -> bytes:
    """Serialize parsed/modified JUMBF box tree back into raw JUMBF bytes."""
    chunks: List[bytes] = []
    for b in boxes:
        tbox = b["type"].encode("latin1")[:4].ljust(4, b" ")
        if tbox == b"jumb" and "children" in b:
            payload = serialize_jumbf_boxes(b["children"])
        elif tbox == b"cbor" and "cbor_raw" in b:
            payload = encode_cbor(b["cbor_raw"])
        elif tbox == b"json" and "json" in b:
            payload = json.dumps(b["json"], ensure_ascii=False).encode("utf-8")
        else:
            payload = b.get("raw_payload", b"")
        lbox = 8 + len(payload)
        chunks.append(struct.pack(">I4s", lbox, tbox) + payload)
    return b"".join(chunks)


def build_jpeg_xt_app11_segments(jumbf_bytes: bytes, en: int = 1) -> List[bytes]:
    """
    Wrap a JUMBF superbox byte stream into one or more JPEG XT (ISO/IEC 19566-5)
    APP11 (0xFFEB) segment payloads (each <= 65533 bytes).
    """
    # Note: In single-packet JPEG XT APP11, payload is:
    # CI (2B 'JP') + En (2B) + Z (4B = 1) + jumbf_bytes
    max_chunk = 65500
    if len(jumbf_bytes) <= max_chunk:
        header = struct.pack(">2sHI", b"JP", en, 1)
        return [header + jumbf_bytes]

    # Multi-packet JPEG XT:
    # First 8 bytes of jumbf_bytes is LBox (4B) + TBox ('jumb', 4B).
    # Each packet repeats CI(2) + En(2) + Z(4) + LBox(4) + TBox(4) followed by slice of box body.
    lbox_tbox = jumbf_bytes[:8]
    body = jumbf_bytes[8:]
    segments: List[bytes] = []
    z = 1
    for i in range(0, len(body), max_chunk):
        part = body[i : i + max_chunk]
        header = struct.pack(">2sHI", b"JP", en, z) + lbox_tbox
        segments.append(header + part)
        z += 1
    return segments


def _parse_cose_signature(cbor_raw: Any) -> Dict[str, Any]:
    """Extract COSE_Sign1 algorithm, X.509 certificate chain, and TSA timestamp info."""
    sig_info: Dict[str, Any] = {}
    val = cbor_raw.value if isinstance(cbor_raw, CBORTag) else cbor_raw
    if not isinstance(val, list) or len(val) < 4:
        return sig_info

    phdr_bytes, uhdr, _payload, sig_bytes = val[0], val[1], val[2], val[3]
    if isinstance(sig_bytes, bytes):
        sig_info["signature_length_bytes"] = len(sig_bytes)
        sig_info["signature_hex_preview"] = sig_bytes[:24].hex() + "..."

    certs_der: List[bytes] = []
    if isinstance(phdr_bytes, bytes) and len(phdr_bytes) > 0:
        try:
            phdr, _ = decode_cbor(phdr_bytes, 0)
            if isinstance(phdr, dict):
                alg_id = phdr.get(1)
                if alg_id is not None:
                    sig_info["algorithm"] = COSE_ALGORITHMS.get(alg_id, f"COSE Alg {alg_id}")
                x5chain = phdr.get(33) or phdr.get("x5chain")
                if isinstance(x5chain, bytes):
                    certs_der = [x5chain]
                elif isinstance(x5chain, list):
                    certs_der = [c for c in x5chain if isinstance(c, bytes)]
        except Exception:
            pass

    if isinstance(uhdr, dict):
        if not certs_der:
            x5chain = uhdr.get(33) or uhdr.get("x5chain")
            if isinstance(x5chain, bytes):
                certs_der = [x5chain]
            elif isinstance(x5chain, list):
                certs_der = [c for c in x5chain if isinstance(c, bytes)]

        # Extract timestamp from sigTst / sigTst2
        for tst_key in ("sigTst2", "sigTst"):
            tst_obj = uhdr.get(tst_key)
            if isinstance(tst_obj, dict):
                tokens = tst_obj.get("tstTokens", [])
                for tok in tokens:
                    if isinstance(tok, dict) and isinstance(tok.get("val"), bytes):
                        raw_tst = tok["val"]
                        # Find ASN.1 GeneralizedTime (YYYYMMDDHHMMSSZ)
                        times = re.findall(rb"(20\d{12}Z)", raw_tst)
                        if times:
                            t_str = times[0].decode("ascii")
                            formatted = (
                                f"{t_str[0:4]}-{t_str[4:6]}-{t_str[6:8]} "
                                f"{t_str[8:10]}:{t_str[10:12]}:{t_str[12:14]} UTC"
                            )
                            sig_info["timestamp_utc"] = formatted
                        tsa_names = re.findall(rb"Google [A-Za-z0-9 \-]+Authority(?: T\d+)?", raw_tst)
                        if tsa_names:
                            sig_info["timestamp_authority"] = tsa_names[0].decode("ascii", errors="replace").strip()

    if certs_der and HAS_CRYPTOGRAPHY:
        cert_list = []
        for idx, c_der in enumerate(certs_der):
            try:
                cert = x509.load_der_x509_certificate(c_der)
                nb = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before
                na = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
                cert_list.append({
                    "index": idx,
                    "role": "Signer Certificate" if idx == 0 else f"Intermediate CA #{idx}",
                    "subject": cert.subject.rfc4514_string(),
                    "issuer": cert.issuer.rfc4514_string(),
                    "serial_number": hex(cert.serial_number),
                    "valid_from": str(nb),
                    "valid_until": str(na),
                })
            except Exception as exc:
                cert_list.append({"index": idx, "error": str(exc)})
        if cert_list:
            sig_info["certificates"] = cert_list
            if "subject" in cert_list[0]:
                sig_info["signer"] = cert_list[0]["subject"]
                sig_info["issuer"] = cert_list[0]["issuer"]

    return sig_info


def extract_c2pa_summary(jumbf_boxes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Walk parsed JUMBF boxes and build a rich, structured C2PA Content Credentials
    report with manifests, claims, AI actions, ingredients, and signatures.
    """
    c2pa_report: Dict[str, Any] = {
        "present": False,
        "store_label": "",
        "manifests": [],
        "active_manifest_id": None,
        "claim_generators": [],
        "ai_actions": [],
        "all_actions": [],
        "digital_source_types": [],
        "synthid_detected": False,
        "signers": [],
        "timestamps": [],
    }
    if not jumbf_boxes:
        return c2pa_report

    for root in jumbf_boxes:
        if root.get("type") != "jumb":
            continue
        if root.get("uuid_type") == "c2pa" or root.get("label") == "c2pa":
            c2pa_report["present"] = True
            c2pa_report["store_label"] = root.get("label", "c2pa")
            manifest_boxes = [
                c for c in root.get("children", [])
                if c.get("type") == "jumb" and c.get("uuid_type") in ("c2ma", "c2pa")
            ]
            for m_idx, mbox in enumerate(manifest_boxes):
                m_label = mbox.get("label", f"manifest_{m_idx}")
                m_info: Dict[str, Any] = {
                    "manifest_id": m_label,
                    "is_active_manifest": (m_idx == len(manifest_boxes) - 1),
                    "claim": {},
                    "actions": [],
                    "ingredients": [],
                    "data_hash": {},
                    "signature": {},
                }
                for child in mbox.get("children", []):
                    clabel = child.get("label", "")
                    cuuid = child.get("uuid_type", "")
                    if cuuid == "c2cl" or "c2pa.claim" in clabel:
                        for sub in child.get("children", []):
                            if sub.get("type") == "cbor" and "cbor" in sub:
                                claim_data = sub["cbor"]
                                m_info["claim"] = claim_data
                                cgi = claim_data.get("claim_generator_info") or claim_data.get("claim_generator")
                                if isinstance(cgi, dict):
                                    gen_str = f"{cgi.get('name', 'Unknown')} ({cgi.get('version', '')})".strip()
                                    if gen_str not in c2pa_report["claim_generators"]:
                                        c2pa_report["claim_generators"].append(gen_str)
                                elif isinstance(cgi, str) and cgi not in c2pa_report["claim_generators"]:
                                    c2pa_report["claim_generators"].append(cgi)
                    elif cuuid == "c2cs" or "c2pa.signature" in clabel:
                        for sub in child.get("children", []):
                            if sub.get("type") == "cbor" and "cbor_raw" in sub:
                                sig_parsed = _parse_cose_signature(sub["cbor_raw"])
                                m_info["signature"] = sig_parsed
                                if sig_parsed.get("signer") and sig_parsed["signer"] not in c2pa_report["signers"]:
                                    c2pa_report["signers"].append(sig_parsed["signer"])
                                if sig_parsed.get("timestamp_utc") and sig_parsed["timestamp_utc"] not in c2pa_report["timestamps"]:
                                    c2pa_report["timestamps"].append(sig_parsed["timestamp_utc"])
                    elif cuuid == "c2as" or "c2pa.assertions" in clabel:
                        for assertion in child.get("children", []):
                            alabel = assertion.get("label", "")
                            cbor_node = next(
                                (s for s in assertion.get("children", []) if s.get("type") == "cbor"),
                                None,
                            )
                            if not cbor_node or "cbor" not in cbor_node:
                                continue
                            adata = cbor_node["cbor"]
                            if "c2pa.actions" in alabel and isinstance(adata, dict):
                                act_list = adata.get("actions", [])
                                for act in act_list:
                                    if not isinstance(act, dict):
                                        continue
                                    dst = act.get("digitalSourceType", "")
                                    dst_human = DIGITAL_SOURCE_TYPE_LABELS.get(dst, dst)
                                    desc = act.get("description", "")
                                    action_name = act.get("action", "")
                                    entry = {
                                        "manifest": m_label,
                                        "action": action_name,
                                        "description": desc,
                                        "digitalSourceType": dst,
                                        "digitalSourceTypeLabel": dst_human,
                                        "softwareAgent": act.get("softwareAgent", ""),
                                    }
                                    m_info["actions"].append(entry)
                                    c2pa_report["all_actions"].append(entry)
                                    if dst_human and dst_human not in c2pa_report["digital_source_types"]:
                                        c2pa_report["digital_source_types"].append(dst_human)
                                    if "synthid" in desc.lower():
                                        c2pa_report["synthid_detected"] = True
                                    if (
                                        "algorithmic" in dst.lower()
                                        or "generative" in desc.lower()
                                        or "synthid" in desc.lower()
                                        or "ai" in desc.lower()
                                    ):
                                        c2pa_report["ai_actions"].append(entry)
                            elif "c2pa.ingredient" in alabel and isinstance(adata, dict):
                                ing_entry = {
                                    "label": alabel,
                                    "relationship": adata.get("relationship", ""),
                                    "description": adata.get("description", ""),
                                    "format": adata.get("dc:format", ""),
                                }
                                val_res = adata.get("validationResults", {}).get("activeManifest", {})
                                if val_res:
                                    ing_entry["validation_success_count"] = len(val_res.get("success", []))
                                    ing_entry["validation_failure_count"] = len(val_res.get("failure", []))
                                    ing_entry["validation_codes"] = [
                                        s.get("code", "") for s in val_res.get("success", []) if isinstance(s, dict)
                                    ]
                                m_info["ingredients"].append(ing_entry)
                            elif "c2pa.hash.data" in alabel and isinstance(adata, dict):
                                m_info["data_hash"] = {
                                    "alg": adata.get("alg", ""),
                                    "hash": adata.get("hash", ""),
                                    "exclusions": adata.get("exclusions", []),
                                }

                c2pa_report["manifests"].append(m_info)
            if c2pa_report["manifests"]:
                c2pa_report["active_manifest_id"] = c2pa_report["manifests"][-1]["manifest_id"]

    return c2pa_report


# ============================================================================
# EXIF (TIFF) Parser & Writer
# ============================================================================

EXIF_TYPE_SIZES: Dict[int, int] = {
    1: 1,   # BYTE
    2: 1,   # ASCII
    3: 2,   # SHORT
    4: 4,   # LONG
    5: 8,   # RATIONAL
    6: 1,   # SBYTE
    7: 1,   # UNDEFINED
    8: 2,   # SSHORT
    9: 4,   # SLONG
    10: 8,  # SRATIONAL
}


def _decode_exif_value(
    tiff: bytes, endian: str, field_type: int, count: int, raw_offset_bytes: bytes, tag_id: int
) -> Any:
    unit = EXIF_TYPE_SIZES.get(field_type, 1)
    total_bytes = unit * count
    if total_bytes <= 4:
        val_bytes = raw_offset_bytes[:total_bytes]
    else:
        offset = struct.unpack(endian + "I", raw_offset_bytes)[0]
        if offset + total_bytes > len(tiff):
            return f"<out-of-bounds offset={offset} len={total_bytes}>"
        val_bytes = tiff[offset : offset + total_bytes]

    # Windows XP UTF-16LE tags
    if tag_id in (0x9C9B, 0x9C9C, 0x9C9D, 0x9C9E, 0x9C9F):
        return val_bytes.decode("utf-16-le", errors="replace").rstrip("\x00")

    if field_type == 2:  # ASCII
        return val_bytes.rstrip(b"\x00").decode("utf-8", errors="replace")
    elif field_type == 1:  # BYTE
        vals = list(val_bytes)
        return vals[0] if count == 1 else vals
    elif field_type == 3:  # SHORT
        vals = list(struct.unpack(f"{endian}{count}H", val_bytes))
        return vals[0] if count == 1 else vals
    elif field_type == 4:  # LONG
        vals = list(struct.unpack(f"{endian}{count}I", val_bytes))
        return vals[0] if count == 1 else vals
    elif field_type == 5:  # RATIONAL
        rats = []
        for i in range(count):
            num, den = struct.unpack(f"{endian}II", val_bytes[i * 8 : (i + 1) * 8])
            rats.append(f"{num}/{den}" if den not in (0, 1) else (str(num) if den == 1 else "0"))
        return rats[0] if count == 1 else rats
    elif field_type == 10:  # SRATIONAL
        rats = []
        for i in range(count):
            num, den = struct.unpack(f"{endian}ii", val_bytes[i * 8 : (i + 1) * 8])
            rats.append(f"{num}/{den}" if den not in (0, 1) else (str(num) if den == 1 else "0"))
        return rats[0] if count == 1 else rats
    elif field_type == 7:  # UNDEFINED
        if tag_id == 0x9286 and len(val_bytes) >= 8:  # UserComment
            prefix = val_bytes[:8]
            body = val_bytes[8:]
            if prefix.startswith(b"UNICODE"):
                # Detect UTF-16BE vs UTF-16LE
                if len(body) >= 2 and body[0] == 0 and body[1] != 0:
                    return body.decode("utf-16-be", errors="replace").rstrip("\x00")
                return body.decode("utf-16-le", errors="replace").rstrip("\x00")
            return body.rstrip(b"\x00").decode("utf-8", errors="replace")
        if tag_id in (0x9000, 0xA000):
            return val_bytes.decode("ascii", errors="replace")
        # Printable check
        try:
            s = val_bytes.rstrip(b"\x00").decode("utf-8")
            if all(32 <= ord(ch) <= 126 or ch in "\r\n\t" for ch in s):
                return s
        except Exception:
            pass
        return f"0x{val_bytes.hex()}"
    return f"0x{val_bytes.hex()}"


def parse_exif_tiff(tiff: bytes) -> Dict[str, Any]:
    """Parse a raw TIFF header + IFDs into a dictionary of EXIF tags."""
    result: Dict[str, Any] = {
        "byte_order": "",
        "IFD0": {},
        "ExifIFD": {},
        "GPSIFD": {},
        "IFD1": {},
        "flat": {},
    }
    if len(tiff) < 8:
        return result
    order = tiff[:2]
    if order == b"II":
        endian = "<"
        result["byte_order"] = "Little Endian (II)"
    elif order == b"MM":
        endian = ">"
        result["byte_order"] = "Big Endian (MM)"
    else:
        return result

    magic, ifd0_offset = struct.unpack(endian + "HI", tiff[2:8])
    if magic != 42:
        return result

    def read_ifd(ifd_offset: int, tag_map: Dict[int, str]) -> Tuple[Dict[str, Any], int]:
        tags_out: Dict[str, Any] = {}
        if ifd_offset == 0 or ifd_offset + 2 > len(tiff):
            return tags_out, 0
        num_entries = struct.unpack(endian + "H", tiff[ifd_offset : ifd_offset + 2])[0]
        pos = ifd_offset + 2
        for _ in range(num_entries):
            if pos + 12 > len(tiff):
                break
            tag_id, ftype, count = struct.unpack(endian + "HHI", tiff[pos : pos + 8])
            raw_off = tiff[pos + 8 : pos + 12]
            pos += 12
            tname = tag_map.get(tag_id, f"Tag_0x{tag_id:04X}")
            val = _decode_exif_value(tiff, endian, ftype, count, raw_off, tag_id)
            tags_out[tname] = val
        next_ifd = 0
        if pos + 4 <= len(tiff):
            next_ifd = struct.unpack(endian + "I", tiff[pos : pos + 4])[0]
        return tags_out, next_ifd

    ifd0, ifd1_off = read_ifd(ifd0_offset, EXIF_TAGS)
    result["IFD0"] = ifd0

    exif_off = ifd0.pop("ExifOffset", None)
    gps_off = ifd0.pop("GPSInfo", None)

    if isinstance(exif_off, int) and exif_off > 0:
        exif_ifd, _ = read_ifd(exif_off, EXIF_TAGS)
        exif_ifd.pop("InteropOffset", None)
        result["ExifIFD"] = exif_ifd

    if isinstance(gps_off, int) and gps_off > 0:
        gps_ifd, _ = read_ifd(gps_off, GPS_TAGS)
        result["GPSIFD"] = gps_ifd

    if isinstance(ifd1_off, int) and ifd1_off > 0:
        ifd1, _ = read_ifd(ifd1_off, EXIF_TAGS)
        result["IFD1"] = ifd1

    for section in ("IFD0", "ExifIFD", "GPSIFD"):
        for k, v in result[section].items():
            result["flat"][k] = v

    return result


def build_exif_tiff(tags: Dict[str, str]) -> bytes:
    """
    Build a standard Big-Endian ('MM') TIFF stream from a dictionary of EXIF tag names -> string values.
    Supports all standard IFD0 and ExifIFD string/comment tags.
    """
    ifd0_entries: List[Tuple[int, int, int, bytes]] = []
    exif_entries: List[Tuple[int, int, int, bytes]] = []

    for name, val_str in tags.items():
        if val_str is None or val_str == "":
            continue
        val_str = str(val_str)
        if name in EDITABLE_EXIF_IFD0_TAGS:
            tag_id, ftype = EDITABLE_EXIF_IFD0_TAGS[name]
            encoded = val_str.encode("utf-8") + b"\x00"
            ifd0_entries.append((tag_id, ftype, len(encoded), encoded))
        elif name in EDITABLE_EXIF_SUB_TAGS:
            tag_id, ftype = EDITABLE_EXIF_SUB_TAGS[name]
            if tag_id == 0x9286:  # UserComment
                # Use ASCII prefix if pure ASCII, else UNICODE UTF-16BE
                try:
                    asc = val_str.encode("ascii")
                    encoded = b"ASCII\x00\x00\x00" + asc
                except UnicodeEncodeError:
                    encoded = b"UNICODE\x00" + val_str.encode("utf-16-be")
                exif_entries.append((tag_id, 7, len(encoded), encoded))
            else:
                encoded = val_str.encode("utf-8") + b"\x00"
                exif_entries.append((tag_id, ftype, len(encoded), encoded))
        elif name == "XPComment":
            encoded = (val_str + "\x00").encode("utf-16-le")
            ifd0_entries.append((0x9C9C, 1, len(encoded), encoded))
        elif name == "XPTitle":
            encoded = (val_str + "\x00").encode("utf-16-le")
            ifd0_entries.append((0x9C9B, 1, len(encoded), encoded))
        elif name == "XPAuthor":
            encoded = (val_str + "\x00").encode("utf-16-le")
            ifd0_entries.append((0x9C9D, 1, len(encoded), encoded))
        elif name == "XPKeywords":
            encoded = (val_str + "\x00").encode("utf-16-le")
            ifd0_entries.append((0x9C9E, 1, len(encoded), encoded))

    if exif_entries:
        # Add ExifVersion 0232 if not present
        exif_entries.append((0x9000, 7, 4, b"0232"))

    # Reserve ExifOffset pointer in IFD0 if exif_entries is non-empty
    has_sub_ifd = len(exif_entries) > 0
    ifd0_count = len(ifd0_entries) + (1 if has_sub_ifd else 0)
    ifd0_header_pos = 8
    ifd0_data_pos = ifd0_header_pos + 2 + ifd0_count * 12 + 4

    # Pack IFD0
    ifd0_Extra = bytearray()
    if has_sub_ifd:
        # Placeholder for ExifOffset (0x8769); we'll compute its exact offset after IFD0 extra data
        pass

    ifd0_entries.sort(key=lambda x: x[0])
    # First compute total size of IFD0 overflow data
    temp_offset = ifd0_data_pos
    for _, _, _, raw_bytes in ifd0_entries:
        if len(raw_bytes) > 4:
            temp_offset += len(raw_bytes) + (len(raw_bytes) % 2)
    sub_ifd_offset = temp_offset

    if has_sub_ifd:
        ifd0_entries.append((0x8769, 4, 1, struct.pack(">I", sub_ifd_offset)))
        ifd0_entries.sort(key=lambda x: x[0])

    ifd0_table = bytearray(struct.pack(">H", len(ifd0_entries)))
    cur_data_offset = ifd0_data_pos
    for tag_id, ftype, count, raw_bytes in ifd0_entries:
        if tag_id == 0x8769:
            ifd0_table.extend(struct.pack(">HHI4s", tag_id, ftype, count, raw_bytes))
        elif len(raw_bytes) <= 4:
            padded = raw_bytes.ljust(4, b"\x00")
            ifd0_table.extend(struct.pack(">HHI4s", tag_id, ftype, count, padded))
        else:
            ifd0_table.extend(struct.pack(">HHII", tag_id, ftype, count, cur_data_offset))
            ifd0_Extra.extend(raw_bytes)
            if len(raw_bytes) % 2 == 1:
                ifd0_Extra.append(0)
            cur_data_offset = ifd0_data_pos + len(ifd0_Extra)

    ifd0_table.extend(struct.pack(">I", 0))  # Next IFD = 0

    sub_ifd_bytes = bytearray()
    if has_sub_ifd:
        exif_entries.sort(key=lambda x: x[0])
        sub_data_start = sub_ifd_offset + 2 + len(exif_entries) * 12 + 4
        sub_table = bytearray(struct.pack(">H", len(exif_entries)))
        sub_extra = bytearray()
        cur_sub_offset = sub_data_start
        for tag_id, ftype, count, raw_bytes in exif_entries:
            if len(raw_bytes) <= 4:
                padded = raw_bytes.ljust(4, b"\x00")
                sub_table.extend(struct.pack(">HHI4s", tag_id, ftype, count, padded))
            else:
                sub_table.extend(struct.pack(">HHII", tag_id, ftype, count, cur_sub_offset))
                sub_extra.extend(raw_bytes)
                if len(raw_bytes) % 2 == 1:
                    sub_extra.append(0)
                cur_sub_offset = sub_data_start + len(sub_extra)
        sub_table.extend(struct.pack(">I", 0))
        sub_ifd_bytes = sub_table + sub_extra

    tiff_header = b"MM" + struct.pack(">HI", 42, 8)
    return bytes(tiff_header + ifd0_table + ifd0_Extra + sub_ifd_bytes)


# ============================================================================
# IPTC / Photoshop 8BIM (APP13) Parser & Writer
# ============================================================================

def parse_app13_photoshop(seg: bytes) -> Dict[str, Any]:
    """Parse JPEG APP13 (Photoshop 3.0 / 8BIM / IPTC) segment."""
    out: Dict[str, Any] = {
        "header": "",
        "resources": [],
        "iptc": {},
    }
    if not seg.startswith(b"Photoshop 3.0\x00"):
        out["raw_preview"] = seg[:120].decode("utf-8", errors="replace")
        return out

    out["header"] = "Photoshop 3.0"
    p = 14
    while p + 12 <= len(seg):
        sig = seg[p : p + 4]
        if sig != b"8BIM":
            break
        res_id = struct.unpack(">H", seg[p + 4 : p + 6])[0]
        name_len = seg[p + 6]
        name_pad = 1 + name_len
        if name_pad % 2 == 1:
            name_pad += 1
        name = seg[p + 7 : p + 7 + name_len].decode("latin1", errors="replace")
        p += 6 + name_pad
        if p + 4 > len(seg):
            break
        data_size = struct.unpack(">I", seg[p : p + 4])[0]
        p += 4
        res_data = seg[p : p + data_size]
        p += data_size + (data_size % 2)

        res_name = PHOTOSHOP_8BIM_NAMES.get(res_id, f"8BIM_0x{res_id:04X}")
        out["resources"].append({
            "id": f"0x{res_id:04X}",
            "name": res_name,
            "pascal_name": name,
            "size": data_size,
        })

        if res_id == 0x0404:
            # Parse IPTC-NAA datasets
            ip = 0
            while ip + 5 <= len(res_data):
                if res_data[ip] != 0x1C:
                    break
                rec = res_data[ip + 1]
                ds = res_data[ip + 2]
                dlen = struct.unpack(">H", res_data[ip + 3 : ip + 5])[0]
                val_bytes = res_data[ip + 5 : ip + 5 + dlen]
                ip += 5 + dlen
                if rec == 2:
                    ds_name = IPTC_RECORD2_TAGS.get(ds, f"Record2_DS{ds}")
                    val_str = val_bytes.decode("utf-8", errors="replace")
                    if ds_name in out["iptc"]:
                        existing = out["iptc"][ds_name]
                        if isinstance(existing, list):
                            existing.append(val_str)
                        else:
                            out["iptc"][ds_name] = [existing, val_str]
                    else:
                        out["iptc"][ds_name] = val_str
                else:
                    key = f"Record{rec}_DS{ds}"
                    out["iptc"][key] = f"0x{val_bytes.hex()}"
    return out


def build_app13_photoshop(iptc_fields: Dict[str, Union[str, List[str]]]) -> bytes:
    """Build a valid JPEG APP13 ('Photoshop 3.0\\x00' + 8BIM 0x0404 IPTC) segment payload."""
    iptc_stream = bytearray()
    for key, val in iptc_fields.items():
        if val is None or val == "" or val == []:
            continue
        ds = IPTC_NAME_TO_DS.get(key)
        if ds is None:
            m = re.match(r"Record2_DS(\d+)", key)
            if m:
                ds = int(m.group(1))
        if ds is None:
            continue
        values = val if isinstance(val, list) else [val]
        for v in values:
            v_str = str(v)
            if not v_str:
                continue
            raw = v_str.encode("utf-8")
            iptc_stream.extend(struct.pack(">BBBH", 0x1C, 2, ds, len(raw)) + raw)

    if not iptc_stream:
        return b""

    bim = bytearray(b"Photoshop 3.0\x00")
    bim.extend(b"8BIM")
    bim.extend(struct.pack(">H", 0x0404))
    bim.extend(b"\x00\x00")  # empty Pascal string padded to 2 bytes
    bim.extend(struct.pack(">I", len(iptc_stream)))
    bim.extend(iptc_stream)
    if len(iptc_stream) % 2 == 1:
        bim.append(0)
    return bytes(bim)


# ============================================================================
# XMP Parser & Writer
# ============================================================================

XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"


def parse_xmp_xml(xml_str: str) -> Dict[str, Any]:
    """Parse XMP RDF/XML into clean key-value dictionary + raw XML."""
    out: Dict[str, Any] = {
        "fields": {},
        "raw_xml": xml_str.strip(),
    }
    try:
        root = ET.fromstring(xml_str.strip())
    except Exception:
        # Try extracting <x:xmpmeta ...>...</x:xmpmeta> or <rdf:RDF ...>...</rdf:RDF>
        m = re.search(r"(<x:xmpmeta[\s\S]*?</x:xmpmeta>|<rdf:RDF[\s\S]*?</rdf:RDF>)", xml_str)
        if not m:
            return out
        try:
            root = ET.fromstring(m.group(1))
        except Exception:
            return out

    def clean_tag(t: str) -> str:
        if "}" in t:
            ns, local = t[1:].split("}", 1)
            prefix_map = {
                "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
                "http://www.w3.org/XML/1998/namespace": "xml",
                "adobe:ns:meta/": "x",
                "http://purl.org/dc/elements/1.1/": "dc",
                "http://ns.adobe.com/xap/1.0/": "xmp",
                "http://ns.adobe.com/photoshop/1.0/": "photoshop",
                "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/": "Iptc4xmpCore",
                "http://iptc.org/std/Iptc4xmpExt/2008-02-29/": "Iptc4xmpExt",
                "http://ns.adobe.com/tiff/1.0/": "tiff",
                "http://ns.adobe.com/exif/1.0/": "exif",
                "http://c2pa.org/manifest": "c2pa",
            }
            p = prefix_map.get(ns, ns.rstrip("/#").split("/")[-1])
            return f"{p}:{local}"
        return t

    for elem in root.iter():
        tag_name = clean_tag(elem.tag)
        # Check attributes on rdf:Description
        for attr_k, attr_v in elem.attrib.items():
            ck = clean_tag(attr_k)
            if not ck.startswith(("xmlns", "rdf:", "xml:", "x:")):
                out["fields"][ck] = attr_v
        # Leaf elements with text
        if len(elem) == 0 and elem.text and elem.text.strip():
            if tag_name in ("rdf:li",):
                continue
            out["fields"][tag_name] = elem.text.strip()
        elif tag_name not in ("x:xmpmeta", "rdf:RDF", "rdf:Description", "rdf:Alt", "rdf:Seq", "rdf:Bag"):
            # Check if it holds rdf:Alt / rdf:Seq / rdf:Bag with rdf:li items
            items = [
                li.text.strip()
                for li in elem.iter()
                if clean_tag(li.tag) == "rdf:li" and li.text and li.text.strip()
            ]
            if items:
                out["fields"][tag_name] = items[0] if len(items) == 1 else items

    return out


def build_xmp_packet(fields: Dict[str, Any]) -> bytes:
    """Build a standard XMP APP1 payload (`http://ns.adobe.com/xap/1.0/\\x00` + RDF/XML)."""
    clean_fields = {k: v for k, v in fields.items() if v is not None and v != "" and v != []}
    if not clean_fields:
        return b""

    from xml.sax.saxutils import escape

    attrs = []
    elements = []
    for k, v in clean_fields.items():
        if k in ("dc:title", "dc:description", "dc:rights"):
            val_str = escape(str(v))
            elements.append(
                f"      <{k}><rdf:Alt><rdf:li xml:lang=\"x-default\">{val_str}</rdf:li></rdf:Alt></{k}>"
            )
        elif k in ("dc:creator",):
            vals = v if isinstance(v, list) else [v]
            lis = "".join(f"<rdf:li>{escape(str(item))}</rdf:li>" for item in vals)
            elements.append(f"      <{k}><rdf:Seq>{lis}</rdf:Seq></{k}>")
        elif k in ("dc:subject",):
            vals = v if isinstance(v, list) else [x.strip() for x in str(v).split(",") if x.strip()]
            lis = "".join(f"<rdf:li>{escape(str(item))}</rdf:li>" for item in vals)
            elements.append(f"      <{k}><rdf:Bag>{lis}</rdf:Bag></{k}>")
        else:
            if ":" not in k:
                k = f"xmp:{k}"
            attrs.append(f'{k}="{escape(str(v), {"\"": "&quot;"})}"')

    attr_str = (" " + " ".join(attrs)) if attrs else ""
    elem_str = ("\n" + "\n".join(elements) + "\n    ") if elements else ""
    xml = (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"\n'
        '           xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
        '           xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
        '           xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"\n'
        '           xmlns:Iptc4xmpCore="http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/"\n'
        '           xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/"\n'
        '           xmlns:tiff="http://ns.adobe.com/tiff/1.0/"\n'
        '           xmlns:exif="http://ns.adobe.com/exif/1.0/">\n'
        f'    <rdf:Description rdf:about=""{attr_str}>{elem_str}</rdf:Description>\n'
        '  </rdf:RDF>\n'
        '</x:xmpmeta>\n'
        '<?xpacket end="w"?>'
    )
    return XMP_HEADER + xml.encode("utf-8")


# ============================================================================
# AI Parameter String Parser (Stable Diffusion / ComfyUI / NovelAI / etc.)
# ============================================================================

def parse_sd_parameters(text: str) -> Dict[str, str]:
    """Parse Automatic1111 / Forge style Stable Diffusion generation parameters."""
    res: Dict[str, str] = {}
    if "Steps:" not in text:
        return res
    lines = text.strip().splitlines()
    last_line = lines[-1]
    neg_idx = -1
    for i, ln in enumerate(lines[:-1]):
        if ln.startswith("Negative prompt:"):
            neg_idx = i
            break
    if neg_idx != -1:
        res["Positive Prompt"] = "\n".join(lines[:neg_idx]).strip()
        res["Negative Prompt"] = "\n".join(lines[neg_idx:-1])[len("Negative prompt:") :].strip()
    else:
        res["Positive Prompt"] = "\n".join(lines[:-1]).strip()

    for part in last_line.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            res[k.strip()] = v.strip()
    return res


# ============================================================================
# Main Image Metadata Analysis & Manipulation
# ============================================================================

def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.2f} KB ({num_bytes:,} B)"
    else:
        return f"{num_bytes / (1024 * 1024):.2f} MB ({num_bytes:,} B)"


def analyze_image(path: Union[str, Path]) -> Dict[str, Any]:
    """
    Analyze an image file and extract all metadata (Technical, AI/C2PA Provenance,
    EXIF, IPTC/8BIM, XMP, JFIF, Comments, PNG Chunks, and Segment Map).
    """
    p = Path(path)
    raw = p.read_bytes()
    stat = p.stat()

    report: Dict[str, Any] = {
        "file_info": {
            "filename": p.name,
            "path": str(p.resolve()),
            "size_bytes": len(raw),
            "size_human": format_size(len(raw)),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "modified_time": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "format": "Unknown",
            "dimensions": "Unknown",
            "width": 0,
            "height": 0,
            "bit_depth": 0,
            "channels": 0,
            "encoding_mode": "",
            "subsampling": "",
        },
        "ai_summary": {
            "is_ai_generated": False,
            "has_c2pa": False,
            "has_synthid": False,
            "has_social_tracking": False,
            "has_any_metadata": False,
            "verdict": "Clean (No Metadata or AI Tags Found)",
            "badges": [],
            "details": {},
        },
        "c2pa": {"present": False},
        "exif": {},
        "iptc": {},
        "xmp": {},
        "jfif": {},
        "comments": [],
        "png_chunks": {},
        "other_segments": [],
        "segments": [],
        "_jumbf_boxes_raw": [],
    }

    if raw.startswith(b"\xFF\xD8"):
        _analyze_jpeg(raw, report)
    elif raw.startswith(b"\x89PNG\r\n\x1a\n"):
        _analyze_png(raw, report)
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        _analyze_webp(raw, report)

    _build_ai_and_metadata_verdict(report)
    return report


def _analyze_jpeg(data: bytes, report: Dict[str, Any]) -> None:
    report["file_info"]["format"] = "JPEG"
    pos = 2
    app11_packets: Dict[int, List[Tuple[int, bytes]]] = {}
    dqt_count = 0
    dht_count = 0

    report["segments"].append({
        "marker": "0xFFD8",
        "name": "SOI (Start of Image)",
        "offset": 0,
        "length": 2,
    })

    while pos < len(data):
        if data[pos] != 0xFF:
            break
        # Skip fill 0xFF bytes
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        marker_pos = pos - 1
        pos += 1

        if marker == 0xD9:  # EOI
            report["segments"].append({
                "marker": "0xFFD9",
                "name": "EOI (End of Image)",
                "offset": marker_pos,
                "length": 2,
            })
            break

        if marker in (0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0x01):
            continue

        if pos + 2 > len(data):
            break
        seg_len = struct.unpack(">H", data[pos : pos + 2])[0]
        seg_data = data[pos + 2 : pos + seg_len]
        mname = JPEG_MARKER_NAMES.get(marker, f"Marker 0xFF{marker:02X}")

        if marker == 0xDA:  # SOS
            eoi_pos = data.rfind(b"\xFF\xD9")
            entropy_len = (eoi_pos - (marker_pos + 2 + seg_len)) if eoi_pos > marker_pos else (len(data) - (marker_pos + 2 + seg_len))
            report["segments"].append({
                "marker": f"0xFF{marker:02X}",
                "name": f"{mname} + Entropy Image Data ({format_size(entropy_len)})",
                "offset": marker_pos,
                "length": 2 + seg_len + max(0, entropy_len),
            })
            break

        report["segments"].append({
            "marker": f"0xFF{marker:02X}",
            "name": mname,
            "offset": marker_pos,
            "length": 2 + seg_len,
        })

        # Parse specific JPEG markers
        if marker == 0xE0 and seg_data.startswith(b"JFIF\x00") and len(seg_data) >= 14:
            v_maj, v_min, units, xdens, ydens, tw, th = struct.unpack(">BBBHHBB", seg_data[5:14])
            unit_str = {0: "Aspect Ratio", 1: "dots/inch (DPI)", 2: "dots/cm"}.get(units, str(units))
            report["jfif"] = {
                "Version": f"{v_maj}.{v_min:02d}",
                "DensityUnits": unit_str,
                "XDensity": xdens,
                "YDensity": ydens,
                "Thumbnail": f"{tw}x{th}",
            }

        elif marker == 0xE1:
            if seg_data.startswith(b"Exif\x00\x00"):
                parsed_exif = parse_exif_tiff(seg_data[6:])
                report["exif"] = parsed_exif
            elif seg_data.startswith(XMP_HEADER):
                xml_str = seg_data[len(XMP_HEADER) :].decode("utf-8", errors="replace")
                report["xmp"] = parse_xmp_xml(xml_str)
            elif b"http://ns.adobe.com/xmp/extension/" in seg_data[:64]:
                report["other_segments"].append({
                    "marker": "0xFFE1",
                    "type": "ExtendedXMP",
                    "size": len(seg_data),
                })

        elif marker == 0xE2:
            if seg_data.startswith(b"ICC_PROFILE\x00"):
                report["other_segments"].append({
                    "marker": "0xFFE2",
                    "type": "ICC Color Profile",
                    "size": len(seg_data),
                })
            else:
                report["other_segments"].append({
                    "marker": "0xFFE2",
                    "type": "APP2 Segment",
                    "size": len(seg_data),
                })

        elif marker == 0xEB:  # APP11 (JPEG XT / JUMBF / C2PA)
            if len(seg_data) >= 8 and seg_data[:2] == b"JP":
                _, en, z = struct.unpack(">2sHI", seg_data[:8])
                app11_packets.setdefault(en, []).append((z, seg_data[8:]))
            else:
                report["other_segments"].append({
                    "marker": "0xFFEB",
                    "type": "APP11",
                    "size": len(seg_data),
                })

        elif marker == 0xED:  # APP13 (Photoshop 8BIM / IPTC)
            report["iptc"] = parse_app13_photoshop(seg_data)

        elif marker == 0xEE:  # APP14 (Adobe)
            report["other_segments"].append({
                "marker": "0xFFEE",
                "type": "Adobe APP14",
                "size": len(seg_data),
                "preview": seg_data[:32].decode("latin1", errors="replace"),
            })

        elif marker == 0xFE:  # COM (Comment)
            com_text = seg_data.decode("utf-8", errors="replace")
            report["comments"].append(com_text)

        elif marker in (0xC0, 0xC1, 0xC2, 0xC3) and len(seg_data) >= 6:
            prec, height, width, comps = struct.unpack(">BHHB", seg_data[:6])
            report["file_info"]["width"] = width
            report["file_info"]["height"] = height
            report["file_info"]["dimensions"] = f"{width} x {height} px"
            report["file_info"]["bit_depth"] = prec
            report["file_info"]["channels"] = comps
            report["file_info"]["encoding_mode"] = JPEG_MARKER_NAMES.get(marker, f"SOF (0xFF{marker:02X})")
            # Chroma subsampling from component sampling factors
            if comps == 3 and len(seg_data) >= 15:
                sf1 = seg_data[7]
                sf2 = seg_data[10]
                sf3 = seg_data[13]
                h1, v1 = (sf1 >> 4) & 0xF, sf1 & 0xF
                h2, v2 = (sf2 >> 4) & 0xF, sf2 & 0xF
                if (h1, v1) == (2, 2) and (h2, v2) == (1, 1):
                    report["file_info"]["subsampling"] = "YCbCr 4:2:0 (2x2, 1x1, 1x1)"
                elif (h1, v1) == (2, 1) and (h2, v2) == (1, 1):
                    report["file_info"]["subsampling"] = "YCbCr 4:2:2 (2x1, 1x1, 1x1)"
                elif (h1, v1) == (1, 1) and (h2, v2) == (1, 1):
                    report["file_info"]["subsampling"] = "YCbCr 4:4:4 (1x1, 1x1, 1x1)"
                else:
                    report["file_info"]["subsampling"] = f"({h1}x{v1}, {h2}x{v2})"

        elif marker == 0xDB:
            dqt_count += 1
        elif marker == 0xC4:
            dht_count += 1

        pos += seg_len

    report["file_info"]["quantization_tables"] = dqt_count
    report["file_info"]["huffman_tables"] = dht_count

    # Reassemble JUMBF streams from APP11 packets
    if app11_packets:
        all_jumbf_boxes: List[Dict[str, Any]] = []
        for en, pkts in sorted(app11_packets.items()):
            pkts.sort(key=lambda x: x[0])
            if len(pkts) == 1:
                stream = pkts[0][1]
            else:
                # First packet has LBox(4) + TBox(4) + payload; subsequent packets repeat LBox(4) + TBox(4)
                parts = [pkts[0][1]]
                for _, pdata in pkts[1:]:
                    parts.append(pdata[8:] if len(pdata) >= 8 and pdata[4:8] == b"jumb" else pdata)
                stream = b"".join(parts)
            boxes = parse_jumbf_boxes(stream, 0, len(stream))
            all_jumbf_boxes.extend(boxes)

        report["_jumbf_boxes_raw"] = all_jumbf_boxes
        report["c2pa"] = extract_c2pa_summary(all_jumbf_boxes)


def _analyze_png(data: bytes, report: Dict[str, Any]) -> None:
    report["file_info"]["format"] = "PNG"
    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8].decode("latin1", errors="replace")
        cdata = data[pos + 8 : pos + 8 + length]
        report["segments"].append({
            "marker": ctype,
            "name": f"PNG Chunk {ctype}",
            "offset": pos,
            "length": 12 + length,
        })
        if ctype == "IHDR" and len(cdata) >= 13:
            w, h, bd, ct = struct.unpack(">IIBB", cdata[:10])
            report["file_info"]["width"] = w
            report["file_info"]["height"] = h
            report["file_info"]["dimensions"] = f"{w} x {h} px"
            report["file_info"]["bit_depth"] = bd
            ct_map = {0: "Grayscale", 2: "RGB", 3: "Indexed", 4: "Grayscale+Alpha", 6: "RGBA"}
            report["file_info"]["encoding_mode"] = ct_map.get(ct, f"ColorType {ct}")
        elif ctype == "tEXt":
            nul = cdata.find(b"\x00")
            if nul != -1:
                k = cdata[:nul].decode("latin1", errors="replace")
                v = cdata[nul + 1 :].decode("utf-8", errors="replace")
                report["png_chunks"][k] = v
        elif ctype == "zTXt":
            nul = cdata.find(b"\x00")
            if nul != -1 and nul + 2 <= len(cdata):
                k = cdata[:nul].decode("latin1", errors="replace")
                try:
                    v = zlib.decompress(cdata[nul + 2 :]).decode("utf-8", errors="replace")
                except Exception:
                    v = "<compressed zTXt>"
                report["png_chunks"][k] = v
        elif ctype == "iTXt":
            nul1 = cdata.find(b"\x00")
            if nul1 != -1 and nul1 + 3 <= len(cdata):
                k = cdata[:nul1].decode("latin1", errors="replace")
                comp_flag = cdata[nul1 + 1]
                rest = cdata[nul1 + 3 :]
                # Skip language tag and translated keyword
                parts = rest.split(b"\x00", 2)
                body = parts[-1]
                if comp_flag == 1:
                    try:
                        val_str = zlib.decompress(body).decode("utf-8", errors="replace")
                    except Exception:
                        val_str = "<compressed iTXt>"
                else:
                    val_str = body.decode("utf-8", errors="replace")
                if k == "XML:com.adobe.xmp":
                    report["xmp"] = parse_xmp_xml(val_str)
                else:
                    report["png_chunks"][k] = val_str
        elif ctype == "eXIf":
            report["exif"] = parse_exif_tiff(cdata)
        elif ctype == "caBX":
            boxes = parse_jumbf_boxes(cdata, 0, len(cdata))
            report["_jumbf_boxes_raw"] = boxes
            report["c2pa"] = extract_c2pa_summary(boxes)
        pos += 12 + length


def _analyze_webp(data: bytes, report: Dict[str, Any]) -> None:
    report["file_info"]["format"] = "WebP"
    pos = 12
    while pos + 8 <= len(data):
        ctype = data[pos : pos + 4].decode("latin1", errors="replace")
        length = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        cdata = data[pos + 8 : pos + 8 + length]
        report["segments"].append({
            "marker": ctype,
            "name": f"WebP Chunk {ctype}",
            "offset": pos,
            "length": 8 + length,
        })
        if ctype == "EXIF":
            tiff_data = cdata[6:] if cdata.startswith(b"Exif\x00\x00") else cdata
            report["exif"] = parse_exif_tiff(tiff_data)
        elif ctype == "XMP ":
            report["xmp"] = parse_xmp_xml(cdata.decode("utf-8", errors="replace"))
        elif ctype == "C2PA":
            boxes = parse_jumbf_boxes(cdata, 0, len(cdata))
            report["_jumbf_boxes_raw"] = boxes
            report["c2pa"] = extract_c2pa_summary(boxes)
        pos += 8 + length + (length % 2)


def _build_ai_and_metadata_verdict(report: Dict[str, Any]) -> None:
    """Synthesize AI tags, provenance info, and social tracking into a high-level summary."""
    ai = report["ai_summary"]
    badges: List[str] = []
    details: Dict[str, Any] = {}

    # 1. Check C2PA Content Credentials
    c2pa = report.get("c2pa", {})
    if c2pa.get("present"):
        ai["has_c2pa"] = True
        ai["has_any_metadata"] = True
        badges.append("C2PA Content Credentials")
        if c2pa.get("claim_generators"):
            details["C2PA Claim Generator"] = ", ".join(c2pa["claim_generators"])
        if c2pa.get("signers"):
            details["C2PA Cryptographic Signer"] = " | ".join(c2pa["signers"])
        if c2pa.get("timestamps"):
            details["C2PA Signing Timestamps"] = ", ".join(c2pa["timestamps"])
        if c2pa.get("digital_source_types"):
            details["Digital Source Type"] = ", ".join(c2pa["digital_source_types"])
        if c2pa.get("all_actions"):
            act_summaries = [
                f"{a['action']}: {a['description']}" if a.get("description") else a["action"]
                for a in c2pa["all_actions"]
            ]
            details["C2PA Provenance Actions"] = act_summaries
        if c2pa.get("ai_actions") or any("algorithmic" in d.lower() for d in c2pa.get("digital_source_types", [])):
            ai["is_ai_generated"] = True
            badges.append("AI-Generated (Trained Algorithmic Media)")
        if c2pa.get("synthid_detected"):
            ai["has_synthid"] = True
            badges.append("Google SynthID Watermark")
        for g in c2pa.get("claim_generators", []):
            if "google" in g.lower() and "Google Generative AI" not in badges:
                badges.append("Google Generative AI")

    # 2. Check EXIF / XMP / IPTC / PNG Chunks / Comments for AI & Tracking
    exif_flat = report.get("exif", {}).get("flat", {})
    if exif_flat:
        ai["has_any_metadata"] = True

    iptc_fields = report.get("iptc", {}).get("iptc", {})
    if iptc_fields or report.get("iptc", {}).get("resources"):
        ai["has_any_metadata"] = True
        spec_instr = str(iptc_fields.get("SpecialInstructions", ""))
        if spec_instr.startswith("FBMD"):
            ai["has_social_tracking"] = True
            badges.append("Facebook/Instagram IPTC Tracking (FBMD)")
            details["Social Media Tracking (IPTC 2:40)"] = f"FBMD Header ({spec_instr[:32]}...)"
        dst_iptc = str(iptc_fields.get("DigitalSourceType", ""))
        if dst_iptc:
            details["IPTC Digital Source Type"] = DIGITAL_SOURCE_TYPE_LABELS.get(dst_iptc, dst_iptc)
            if "algorithmic" in dst_iptc.lower():
                ai["is_ai_generated"] = True
                badges.append("IPTC AI Source Tag")

    xmp_fields = report.get("xmp", {}).get("fields", {})
    if xmp_fields:
        ai["has_any_metadata"] = True
        for k, v in xmp_fields.items():
            kl = k.lower()
            vl = str(v).lower()
            if "digitalsourcetype" in kl:
                details["XMP Digital Source Type"] = DIGITAL_SOURCE_TYPE_LABELS.get(str(v), str(v))
                if "algorithmic" in vl:
                    ai["is_ai_generated"] = True
                    badges.append("XMP AI Source Tag")
            if any(term in vl for term in ("stable diffusion", "midjourney", "dall-e", "firefly", "generative ai", "synthid", "comfyui")):
                ai["is_ai_generated"] = True
                details[f"XMP {k}"] = v

    # Check EXIF Software / ImageDescription / UserComment / Artist
    for field_name in ("Software", "ImageDescription", "UserComment", "Artist", "XPComment", "XPKeywords"):
        val = str(exif_flat.get(field_name, ""))
        if not val:
            continue
        if "Steps:" in val and ("Sampler:" in val or "CFG scale:" in val):
            ai["is_ai_generated"] = True
            badges.append("Stable Diffusion Parameters")
            details["Stable Diffusion Parameters"] = parse_sd_parameters(val)
        elif any(term in val.lower() for term in ("stable diffusion", "midjourney", "dall-e", "firefly", "google generative ai", "synthid", "comfyui", "novelai")):
            ai["is_ai_generated"] = True
            details[f"EXIF {field_name}"] = val

    # Check JPEG comments & PNG text chunks
    for c in report.get("comments", []):
        ai["has_any_metadata"] = True
        if "Steps:" in c:
            ai["is_ai_generated"] = True
            badges.append("Stable Diffusion Parameters")
            details["Stable Diffusion Parameters"] = parse_sd_parameters(c)
        elif any(term in c.lower() for term in ("ai", "generative", "midjourney", "stable diffusion", "synthid")):
            ai["is_ai_generated"] = True
            details["JPEG Comment AI Tag"] = c

    for k, v in report.get("png_chunks", {}).items():
        ai["has_any_metadata"] = True
        if k == "parameters" or "Steps:" in str(v):
            ai["is_ai_generated"] = True
            badges.append("Stable Diffusion Parameters")
            details["Stable Diffusion Parameters"] = parse_sd_parameters(str(v))
        elif k in ("prompt", "workflow"):
            ai["is_ai_generated"] = True
            if "ComfyUI Workflow" not in badges:
                badges.append("ComfyUI Workflow")
            details[f"ComfyUI {k}"] = str(v)[:300] + ("..." if len(str(v)) > 300 else "")

    if report.get("other_segments"):
        ai["has_any_metadata"] = True

    ai["badges"] = badges
    ai["details"] = details

    # Verdict string
    if ai["is_ai_generated"]:
        parts = []
        if "Google Generative AI" in badges:
            parts.append("Google Generative AI")
        if ai["has_synthid"]:
            parts.append("SynthID Watermark")
        if ai["has_c2pa"]:
            parts.append("C2PA Provenance")
        if not parts:
            parts = badges or ["AI Tags Present"]
        ai["verdict"] = f"AI-GENERATED IMAGE ({' + '.join(parts)})"
    elif ai["has_social_tracking"]:
        ai["verdict"] = "SOCIAL MEDIA METADATA DETECTED (Facebook/Instagram IPTC FBMD Tag)"
    elif ai["has_any_metadata"]:
        ai["verdict"] = "STANDARD METADATA PRESENT (No AI Tags Detected)"
    else:
        ai["verdict"] = "CLEAN IMAGE (Only Basic JFIF/Image Header, No Metadata/AI Tags)"


# ============================================================================
# Metadata Stripping (Lossless Removal)
# ============================================================================

def strip_all_metadata(
    path: Union[str, Path],
    output_path: Optional[Union[str, Path]] = None,
    *,
    remove_c2pa: bool = True,
    remove_exif: bool = True,
    remove_xmp: bool = True,
    remove_iptc: bool = True,
    remove_com: bool = True,
    remove_other_app: bool = True,
) -> Dict[str, Any]:
    """
    Losslessly remove metadata segments from an image without re-encoding image pixels.
    By default removes ALL metadata (C2PA/JUMBF APP11, EXIF/XMP APP1, IPTC/8BIM APP13,
    Comments COM, ICC/Adobe APP2-APP15), keeping only essential image decoding tables
    and JFIF header.
    """
    src = Path(path)
    dst = Path(output_path) if output_path else src
    data = src.read_bytes()
    orig_size = len(data)
    removed_segments: List[str] = []

    if data.startswith(b"\xFF\xD8"):
        out = bytearray(b"\xFF\xD8")
        # Write a clean minimal JFIF APP0 segment if original had one
        pos = 2
        while pos < len(data):
            if data[pos] != 0xFF:
                out.extend(data[pos:])
                break
            marker_start = pos
            while pos < len(data) and data[pos] == 0xFF:
                pos += 1
            if pos >= len(data):
                break
            marker = data[pos]
            pos += 1

            if marker == 0xD9:
                out.extend(b"\xFF\xD9")
                break
            if marker in (0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0x01):
                out.extend(data[marker_start:pos])
                continue
            if pos + 2 > len(data):
                out.extend(data[marker_start:])
                break
            seg_len = struct.unpack(">H", data[pos : pos + 2])[0]
            seg_end = pos + seg_len
            seg_payload = data[pos + 2 : seg_end]

            if marker == 0xDA:  # SOS -> copy rest of image up to EOI
                out.extend(data[marker_start:])
                break

            drop = False
            reason = ""
            if marker == 0xE0:
                # Keep standard 16-byte JFIF header (strip JFXX or embedded thumbnail if any)
                if seg_payload.startswith(b"JFIF\x00") and len(seg_payload) > 14:
                    clean_jfif = seg_payload[:12] + b"\x00\x00"
                    out.extend(b"\xFF\xE0" + struct.pack(">H", len(clean_jfif) + 2) + clean_jfif)
                    pos = seg_end
                    continue
                elif not seg_payload.startswith(b"JFIF\x00") and remove_other_app:
                    drop = True
                    reason = "APP0 Extension"
            elif marker == 0xE1:
                if seg_payload.startswith(b"Exif\x00\x00") and remove_exif:
                    drop = True
                    reason = "APP1 (EXIF)"
                elif (seg_payload.startswith(XMP_HEADER) or b"ns.adobe.com/xmp" in seg_payload[:64]) and remove_xmp:
                    drop = True
                    reason = "APP1 (XMP)"
                elif remove_other_app:
                    drop = True
                    reason = "APP1"
            elif marker == 0xEB and remove_c2pa:
                drop = True
                reason = "APP11 (C2PA / JUMBF AI Content Credentials)"
            elif marker == 0xED and remove_iptc:
                drop = True
                reason = "APP13 (Photoshop 8BIM / IPTC)"
            elif marker == 0xFE and remove_com:
                drop = True
                reason = "COM (JPEG Comment)"
            elif (0xE2 <= marker <= 0xEF) and marker not in (0xEB, 0xED) and remove_other_app:
                drop = True
                reason = JPEG_MARKER_NAMES.get(marker, f"APP{marker - 0xE0}")

            if drop:
                removed_segments.append(f"{reason} ({seg_len + 2} bytes)")
            else:
                out.extend(data[marker_start:seg_end])
            pos = seg_end

        new_bytes = bytes(out)

    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        out = bytearray(b"\x89PNG\r\n\x1a\n")
        pos = 8
        while pos + 12 <= len(data):
            length = struct.unpack(">I", data[pos : pos + 4])[0]
            ctype = data[pos + 4 : pos + 8].decode("latin1", errors="replace")
            chunk_total = 12 + length
            drop = False
            if ctype in ("tEXt", "zTXt", "iTXt") and (remove_com or remove_xmp):
                drop = True
            elif ctype == "eXIf" and remove_exif:
                drop = True
            elif ctype == "caBX" and remove_c2pa:
                drop = True
            elif ctype == "tIME" and remove_other_app:
                drop = True

            if drop:
                removed_segments.append(f"PNG {ctype} ({chunk_total} bytes)")
            else:
                out.extend(data[pos : pos + chunk_total])
            pos += chunk_total
        new_bytes = bytes(out)
    else:
        raise ValueError(f"Unsupported image format for lossless stripping: {src.name}")

    dst.write_bytes(new_bytes)
    return {
        "path": str(dst.resolve()),
        "original_size": orig_size,
        "new_size": len(new_bytes),
        "bytes_saved": orig_size - len(new_bytes),
        "removed_segments": removed_segments,
    }


# ============================================================================
# Metadata Editing (Lossless Modification of Existing & New Metadata)
# ============================================================================

def get_editable_fields(report: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """
    Return a structured dictionary of currently populated + standard editable fields
    grouped by category: 'exif', 'iptc', 'xmp', 'comment', 'c2pa'.
    """
    exif_flat = report.get("exif", {}).get("flat", {})
    iptc_map = report.get("iptc", {}).get("iptc", {})
    xmp_map = report.get("xmp", {}).get("fields", {})
    comments = report.get("comments", [])
    c2pa = report.get("c2pa", {})

    editable: Dict[str, Dict[str, str]] = {
        "exif": {
            "ImageDescription": str(exif_flat.get("ImageDescription", "")),
            "Artist": str(exif_flat.get("Artist", "")),
            "Copyright": str(exif_flat.get("Copyright", "")),
            "Software": str(exif_flat.get("Software", "")),
            "Make": str(exif_flat.get("Make", "")),
            "Model": str(exif_flat.get("Model", "")),
            "DateTime": str(exif_flat.get("DateTime", "")),
            "DateTimeOriginal": str(exif_flat.get("DateTimeOriginal", "")),
            "UserComment": str(exif_flat.get("UserComment", "")),
        },
        "iptc": {
            "ObjectName": str(iptc_map.get("ObjectName", "")),
            "Headline": str(iptc_map.get("Headline", "")),
            "CaptionAbstract": str(iptc_map.get("CaptionAbstract", "")),
            "Byline": str(iptc_map.get("Byline", "")),
            "Credit": str(iptc_map.get("Credit", "")),
            "Source": str(iptc_map.get("Source", "")),
            "CopyrightNotice": str(iptc_map.get("CopyrightNotice", "")),
            "Keywords": (
                ", ".join(iptc_map["Keywords"])
                if isinstance(iptc_map.get("Keywords"), list)
                else str(iptc_map.get("Keywords", ""))
            ),
            "SpecialInstructions": str(iptc_map.get("SpecialInstructions", "")),
            "DigitalSourceType": str(iptc_map.get("DigitalSourceType", "")),
        },
        "xmp": {
            "dc:title": str(xmp_map.get("dc:title", "")),
            "dc:description": str(xmp_map.get("dc:description", "")),
            "dc:creator": (
                ", ".join(xmp_map["dc:creator"])
                if isinstance(xmp_map.get("dc:creator"), list)
                else str(xmp_map.get("dc:creator", ""))
            ),
            "dc:rights": str(xmp_map.get("dc:rights", "")),
            "dc:subject": (
                ", ".join(xmp_map["dc:subject"])
                if isinstance(xmp_map.get("dc:subject"), list)
                else str(xmp_map.get("dc:subject", ""))
            ),
            "xmp:CreatorTool": str(xmp_map.get("xmp:CreatorTool", "")),
            "photoshop:Credit": str(xmp_map.get("photoshop:Credit", "")),
            "Iptc4xmpExt:DigitalSourceType": str(xmp_map.get("Iptc4xmpExt:DigitalSourceType", "")),
        },
        "comment": {
            "Comment": "\n".join(comments) if comments else "",
        },
        "c2pa": {},
    }

    if c2pa.get("present"):
        # Expose editable C2PA claim generator and action descriptions/source types
        manifests = c2pa.get("manifests", [])
        for m_idx, m in enumerate(manifests):
            cgi = m.get("claim", {}).get("claim_generator_info", {})
            if isinstance(cgi, dict):
                editable["c2pa"][f"Manifest[{m_idx}].GeneratorName"] = str(cgi.get("name", ""))
                editable["c2pa"][f"Manifest[{m_idx}].GeneratorVersion"] = str(cgi.get("version", ""))
            for a_idx, act in enumerate(m.get("actions", [])):
                editable["c2pa"][f"Manifest[{m_idx}].Action[{a_idx}].action"] = str(act.get("action", ""))
                editable["c2pa"][f"Manifest[{m_idx}].Action[{a_idx}].description"] = str(act.get("description", ""))
                editable["c2pa"][f"Manifest[{m_idx}].Action[{a_idx}].digitalSourceType"] = str(
                    act.get("digitalSourceType", "")
                )

    return editable


def _apply_c2pa_edits_to_jumbf(
    jumbf_boxes: List[Dict[str, Any]], c2pa_edits: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Mutate decoded CBOR structures inside `jumbf_boxes` according to `c2pa_edits`."""
    for root in jumbf_boxes:
        if root.get("type") != "jumb":
            continue
        if root.get("uuid_type") == "c2pa" or root.get("label") == "c2pa":
            manifest_boxes = [
                c for c in root.get("children", [])
                if c.get("type") == "jumb" and c.get("uuid_type") in ("c2ma", "c2pa")
            ]
            for m_idx, mbox in enumerate(manifest_boxes):
                for child in mbox.get("children", []):
                    clabel = child.get("label", "")
                    cuuid = child.get("uuid_type", "")
                    if cuuid == "c2cl" or "c2pa.claim" in clabel:
                        for sub in child.get("children", []):
                            if sub.get("type") == "cbor" and isinstance(sub.get("cbor_raw"), dict):
                                raw_claim = sub["cbor_raw"]
                                cgi = raw_claim.get("claim_generator_info")
                                if isinstance(cgi, dict):
                                    gname_key = f"Manifest[{m_idx}].GeneratorName"
                                    gver_key = f"Manifest[{m_idx}].GeneratorVersion"
                                    if gname_key in c2pa_edits:
                                        cgi["name"] = c2pa_edits[gname_key]
                                    if gver_key in c2pa_edits:
                                        cgi["version"] = c2pa_edits[gver_key]
                    elif cuuid == "c2as" or "c2pa.assertions" in clabel:
                        for assertion in child.get("children", []):
                            alabel = assertion.get("label", "")
                            if "c2pa.actions" in alabel:
                                for sub in assertion.get("children", []):
                                    if sub.get("type") == "cbor" and isinstance(sub.get("cbor_raw"), dict):
                                        act_list = sub["cbor_raw"].get("actions", [])
                                        for a_idx, act in enumerate(act_list):
                                            if not isinstance(act, dict):
                                                continue
                                            k_act = f"Manifest[{m_idx}].Action[{a_idx}].action"
                                            k_desc = f"Manifest[{m_idx}].Action[{a_idx}].description"
                                            k_dst = f"Manifest[{m_idx}].Action[{a_idx}].digitalSourceType"
                                            if k_act in c2pa_edits:
                                                act["action"] = c2pa_edits[k_act]
                                            if k_desc in c2pa_edits:
                                                act["description"] = c2pa_edits[k_desc]
                                            if k_dst in c2pa_edits:
                                                if c2pa_edits[k_dst]:
                                                    act["digitalSourceType"] = c2pa_edits[k_dst]
                                                else:
                                                    act.pop("digitalSourceType", None)
    return jumbf_boxes


def update_image_metadata(
    path: Union[str, Path],
    edits: Dict[str, Dict[str, Any]],
    output_path: Optional[Union[str, Path]] = None,
    *,
    remove_c2pa: bool = False,
) -> Dict[str, Any]:
    """
    Update metadata on a JPEG or PNG image losslessly.
    `edits` can contain keys: 'exif', 'iptc', 'xmp', 'comment', 'c2pa'.
    Setting a field's value to "" (empty string) removes that field.
    """
    src = Path(path)
    dst = Path(output_path) if output_path else src
    report = analyze_image(src)
    current = get_editable_fields(report)

    # Merge edits into current values
    merged: Dict[str, Dict[str, Any]] = {}
    for cat in ("exif", "iptc", "xmp", "comment", "c2pa"):
        merged[cat] = dict(current.get(cat, {}))
        if cat in edits and isinstance(edits[cat], dict):
            for k, v in edits[cat].items():
                merged[cat][k] = v

    # Handle keyword list splitting for IPTC
    if "Keywords" in merged["iptc"] and isinstance(merged["iptc"]["Keywords"], str):
        kw_str = merged["iptc"]["Keywords"].strip()
        merged["iptc"]["Keywords"] = [x.strip() for x in kw_str.split(",") if x.strip()] if kw_str else []

    raw = src.read_bytes()
    if raw.startswith(b"\xFF\xD8"):
        # Build updated segments
        new_segments: List[bytes] = []

        # 1. Keep or build APP0 JFIF
        # 2. Build APP1 EXIF if any non-empty EXIF fields exist
        exif_non_empty = {k: v for k, v in merged["exif"].items() if v is not None and str(v).strip() != ""}
        if exif_non_empty:
            tiff_bytes = build_exif_tiff(exif_non_empty)
            exif_payload = b"Exif\x00\x00" + tiff_bytes
            new_segments.append(b"\xFF\xE1" + struct.pack(">H", len(exif_payload) + 2) + exif_payload)

        # 3. Build APP1 XMP if any non-empty XMP fields exist
        xmp_non_empty = {k: v for k, v in merged["xmp"].items() if v is not None and str(v).strip() != ""}
        if xmp_non_empty:
            xmp_payload = build_xmp_packet(xmp_non_empty)
            if xmp_payload:
                new_segments.append(b"\xFF\xE1" + struct.pack(">H", len(xmp_payload) + 2) + xmp_payload)

        # 4. Build APP11 C2PA if present and not removed
        if report["c2pa"].get("present") and not remove_c2pa and report.get("_jumbf_boxes_raw"):
            boxes = report["_jumbf_boxes_raw"]
            if "c2pa" in edits and edits["c2pa"]:
                boxes = _apply_c2pa_edits_to_jumbf(boxes, merged["c2pa"])
            jumbf_stream = serialize_jumbf_boxes(boxes)
            for app11_payload in build_jpeg_xt_app11_segments(jumbf_stream, en=1):
                new_segments.append(b"\xFF\xEB" + struct.pack(">H", len(app11_payload) + 2) + app11_payload)

        # 5. Build APP13 IPTC / Photoshop 8BIM if any non-empty IPTC fields exist
        iptc_payload = build_app13_photoshop(merged["iptc"])
        if iptc_payload:
            new_segments.append(b"\xFF\xED" + struct.pack(">H", len(iptc_payload) + 2) + iptc_payload)

        # 6. Build COM (Comment) if non-empty
        com_str = str(merged["comment"].get("Comment", "")).strip()
        if com_str:
            com_bytes = com_str.encode("utf-8")[:65530]
            new_segments.append(b"\xFF\xFE" + struct.pack(">H", len(com_bytes) + 2) + com_bytes)

        # Reassemble JPEG: SOI + APP0 (if present) + new_segments + non-replaced image segments (DQT, SOF, DHT, SOS...)
        out = bytearray(b"\xFF\xD8")
        inserted_meta = False
        pos = 2
        while pos < len(raw):
            if raw[pos] != 0xFF:
                if not inserted_meta:
                    for s in new_segments:
                        out.extend(s)
                    inserted_meta = True
                out.extend(raw[pos:])
                break
            marker_start = pos
            while pos < len(raw) and raw[pos] == 0xFF:
                pos += 1
            if pos >= len(raw):
                break
            marker = raw[pos]
            pos += 1
            if marker == 0xD9:
                if not inserted_meta:
                    for s in new_segments:
                        out.extend(s)
                    inserted_meta = True
                out.extend(b"\xFF\xD9")
                break
            if marker in (0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0x01):
                out.extend(raw[marker_start:pos])
                continue
            seg_len = struct.unpack(">H", raw[pos : pos + 2])[0]
            seg_end = pos + seg_len
            seg_payload = raw[pos + 2 : seg_end]

            if marker == 0xE0:
                out.extend(raw[marker_start:seg_end])
                if not inserted_meta:
                    for s in new_segments:
                        out.extend(s)
                    inserted_meta = True
                pos = seg_end
                continue

            if not inserted_meta:
                for s in new_segments:
                    out.extend(s)
                inserted_meta = True

            # Skip old EXIF, XMP, APP11 (C2PA), APP13 (IPTC), COM segments since we rebuilt them
            if marker == 0xE1 and (seg_payload.startswith(b"Exif\x00\x00") or seg_payload.startswith(XMP_HEADER)):
                pos = seg_end
                continue
            if marker in (0xEB, 0xED, 0xFE):
                pos = seg_end
                continue

            if marker == 0xDA:
                out.extend(raw[marker_start:])
                break

            out.extend(raw[marker_start:seg_end])
            pos = seg_end

        dst.write_bytes(bytes(out))
        return {"path": str(dst.resolve()), "size": len(out)}

    elif raw.startswith(b"\x89PNG\r\n\x1a\n"):
        # Rebuild PNG with updated eXIf, iTXt (XMP), tEXt (Comment), and caBX (C2PA)
        out = bytearray(b"\x89PNG\r\n\x1a\n")
        pos = 8

        def make_png_chunk(ctype: bytes, cdata: bytes) -> bytes:
            crc = zlib.crc32(ctype + cdata) & 0xFFFFFFFF
            return struct.pack(">I", len(cdata)) + ctype + cdata + struct.pack(">I", crc)

        extra_chunks: List[bytes] = []
        exif_non_empty = {k: v for k, v in merged["exif"].items() if v is not None and str(v).strip() != ""}
        if exif_non_empty:
            extra_chunks.append(make_png_chunk(b"eXIf", build_exif_tiff(exif_non_empty)))
        xmp_non_empty = {k: v for k, v in merged["xmp"].items() if v is not None and str(v).strip() != ""}
        if xmp_non_empty:
            xmp_pkt = build_xmp_packet(xmp_non_empty)
            if xmp_pkt:
                xml_body = xmp_pkt[len(XMP_HEADER) :]
                itxt_data = b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + xml_body
                extra_chunks.append(make_png_chunk(b"iTXt", itxt_data))
        com_str = str(merged["comment"].get("Comment", "")).strip()
        if com_str:
            extra_chunks.append(make_png_chunk(b"tEXt", b"Comment\x00" + com_str.encode("utf-8")))

        inserted = False
        while pos + 12 <= len(raw):
            length = struct.unpack(">I", raw[pos : pos + 4])[0]
            ctype = raw[pos + 4 : pos + 8]
            chunk_total = 12 + length
            if ctype == b"IHDR":
                out.extend(raw[pos : pos + chunk_total])
                for ec in extra_chunks:
                    out.extend(ec)
                inserted = True
            elif ctype in (b"eXIf", b"tEXt") or (ctype == b"caBX" and remove_c2pa):
                pass
            else:
                out.extend(raw[pos : pos + chunk_total])
            pos += chunk_total
        dst.write_bytes(bytes(out))
        return {"path": str(dst.resolve()), "size": len(out)}

    raise ValueError(f"Unsupported image format for editing: {src.name}")


def list_images_in_folder(folder: Union[str, Path]) -> List[Path]:
    """Return sorted list of image files in `folder`."""
    p = Path(folder)
    if not p.exists():
        return []
    if p.is_file():
        return [p]
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    return sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in exts)
