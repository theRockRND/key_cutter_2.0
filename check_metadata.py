#!/usr/bin/env python3
"""Script for checking metadata in JPEG files"""
import sys
from pathlib import Path
import piexif
from PIL import Image

def check_metadata(image_path):
    """Checks metadata in JPEG file"""
    print(f"\n{'='*60}")
    print(f"File: {image_path.name}")
    print(f"{'='*60}")
    
    try:
        # Load EXIF data
        exif_dict = piexif.load(str(image_path))
        
        # Check Title (ImageDescription)
        if piexif.ImageIFD.ImageDescription in exif_dict["0th"]:
            title = exif_dict["0th"][piexif.ImageIFD.ImageDescription].decode('utf-8')
            print(f"Title (ImageDescription): {title}")
        else:
            print("Title (ImageDescription): NOT FOUND")
        
        # Check Windows XP Title
        if piexif.ImageIFD.XPTitle in exif_dict["0th"]:
            xp_title = exif_dict["0th"][piexif.ImageIFD.XPTitle].decode('utf-16le').rstrip('\x00')
            print(f"Windows XP Title: {xp_title}")
        else:
            print("Windows XP Title: NOT FOUND")
        
        # Check Description (UserComment)
        if piexif.ExifIFD.UserComment in exif_dict["Exif"]:
            user_comment = exif_dict["Exif"][piexif.ExifIFD.UserComment]
            if user_comment.startswith(b'unicode'):
                description = user_comment[8:].decode('utf-16le').rstrip('\x00')
            else:
                description = user_comment.decode('utf-8', errors='ignore')
            print(f"Description (UserComment): {description}")
        else:
            print("Description (UserComment): NOT FOUND")
        
        # Check Windows XP Comment
        if piexif.ImageIFD.XPComment in exif_dict["0th"]:
            xp_comment = exif_dict["0th"][piexif.ImageIFD.XPComment].decode('utf-16le').rstrip('\x00')
            print(f"Windows XP Comment: {xp_comment}")
        else:
            print("Windows XP Comment: NOT FOUND")
        
        # Check Keywords (XPKeywords)
        if piexif.ImageIFD.XPKeywords in exif_dict["0th"]:
            keywords_bytes = exif_dict["0th"][piexif.ImageIFD.XPKeywords]
            keywords = keywords_bytes.decode('utf-16le').rstrip('\x00').split('\x00')
            keywords = [kw for kw in keywords if kw]
            print(f"Keywords (XPKeywords): {', '.join(keywords)}")
        else:
            print("Keywords (XPKeywords): NOT FOUND")
        
        # Show all available tags
        print(f"\nAll EXIF tags in file:")
        print(f"0th IFD: {len(exif_dict['0th'])} tags")
        print(f"Exif IFD: {len(exif_dict['Exif'])} tags")
        
    except Exception as e:
        print(f"Error reading metadata: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python check_metadata.py <path_to_jpeg_file>")
        sys.exit(1)
    
    image_path = Path(sys.argv[1])
    if not image_path.exists():
        print(f"File not found: {image_path}")
        sys.exit(1)
    
    check_metadata(image_path)



