# Copyright 2022 Anirudh Topiwala

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
A web application to visualize sound in images.
"""
import io
import logging
import multiprocessing
import random
import tempfile

import altair as alt
import cv2
import numpy as np
import pandas as pd
import pydub
import streamlit as st
from moviepy.editor import (AudioFileClip, ImageClip, VideoClip,
                            concatenate_videoclips)
from PIL import Image
from pytube import YouTube
from streamlit_webrtc import RTCConfiguration, WebRtcMode, webrtc_streamer

logger = logging.getLogger(__name__)

sample_images = ["files/trees.jpg", "files/waterfall_orig.jpg"]
sample_images_mask = ["files/trees_mask.png", "files/waterfall_mask.png"]

RTC_CONFIGURATION = RTCConfiguration(
    {"iceServers": [{
        "urls": ["stun:stun.l.google.com:19302"]
    }]})


def adjust_brightness(img, value):
    img = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    img = img.astype(np.float32)
    img[:, :, 2] *= value
    img = np.uint8(img)
    return cv2.cvtColor(img, cv2.COLOR_HSV2RGB)


@st.cache
def process_image(img, img_mask):
    assert(img is not None)
    # Resize image if its too large.
    img_height, img_width, channels = img.shape
    while img_width > 500 or img_height > 500:
        img_width = int(img_width / 2)
        img_height = int(img_height / 2)

    img = cv2.resize(img, (img_width,img_height))
    if img_mask is None:
        # If no mask is given, the given image becomes image foreground, as thats where the sound is encoded.
        img_foreground = img
        img_background = np.zeros((img_height, img_width, 3), dtype=np.uint8)
    else:
        img_mask = cv2.resize(img_mask, (img_width,img_height))
        # Generate a binary image from the RGB mask.
        binary_mask = (cv2.cvtColor(img_mask, cv2.COLOR_RGB2GRAY) > 0)
        binary_mask = np.uint8(binary_mask * 255)
        # Use the mask to generate a foreground and backgrund image. Sound will be encoded in the image foreground.
        img_foreground = cv2.bitwise_or(img, img, mask=binary_mask)
        img_background = cv2.bitwise_or(img,img,mask=cv2.bitwise_not(binary_mask))


    # Add Sign.
    sign_img = cv2.imread("files/sign.png", cv2.IMREAD_GRAYSCALE)
    sign_img = ((sign_img > 0) * 255).astype(np.uint8)
    # A scale of 0.15 usually works when working with images less than 500px rnage.
    scale = 0.15
    adjusted_height = int(sign_img.shape[0] * scale)
    adjusted_width = int(sign_img.shape[1] * scale)
    resized_sign_img = cv2.resize(sign_img, (adjusted_width, adjusted_height))
    binary_sign_img = np.zeros((img_height, img_width), dtype=np.uint8)
    binary_sign_img[img_height - adjusted_height - 10:img_height - 10,
              img_width - adjusted_width - 10:img_width - 10] = resized_sign_img
    #  Get rgb image.
    binary_sign_img = np.stack((binary_sign_img, binary_sign_img, binary_sign_img), axis=2)
    # Watermark the sign.
    cv2.addWeighted(img_background, 1.0, binary_sign_img, 0.5, 0, img_background)
    return img, img_foreground, img_background

def draw_horz_sound(img, amplitudes):
    img_height, img_width, _  = img.shape
    pos_x = 20
    pos_y = img_height - 50
    box_width_px = img_width / 4
    box_height_px = img_height / 5
    num_points = len(amplitudes)
    step_size_in_x = box_width_px / num_points
    step_size_in_y = box_height_px / 6
    line_thickness = 2
    color = (255, 255, 255)
    for i in range(0, len(amplitudes) - 2):
        point1 = (round(pos_x + (i * step_size_in_x)),
                  round(pos_y -
                        (amplitudes[i].item() * step_size_in_y)))
        point2 = (round(pos_x + ((i + 1) * step_size_in_x)),
                  round(pos_y -
                        (amplitudes[i + 1].item() * step_size_in_y)))
        cv2.line(img, point1, point2, color, thickness=line_thickness)

def encode_image(amplitudes_per_img_frame, img_foreground, img_background, should_plot=False):
    # Max amplitude represents maximum deviation of brightness.
    max_val = max(amplitudes_per_img_frame, key=abs)
    # This is not usally required, although clipping to remove noise.
    max_val = np.clip(max_val, -0.3, 0.3)
    # Negative wave usually has a stronger amplitude from experimenting.
    max_val = -max_val
    img_foreground = adjust_brightness(img_foreground, max_val + 0.7)
    merged_image = np.add(img_foreground, img_background)
    if should_plot:
        draw_horz_sound(merged_image, amplitudes_per_img_frame)
    return np.asarray(merged_image, dtype=np.uint8)

def load_image():
    st.sidebar.markdown("---")
    st.sidebar.markdown(
        """Please upload an image or use an exisiting example.""")
    img = None
    img_mask = None

    # Upload an Image or use the defaults.
    uploaded_img_file = st.sidebar.file_uploader(
        "Choose a File",
        type=["png", "jpg", "jpeg"],
    )
    # Optionally upload image mask to restrict area in which sound is visualized.
    uploaded_img_mask = st.sidebar.file_uploader(
        "Optionally upload an image mask to restrict the area in which sound is visualized. (Size should match that of input image)",
        type=["png", "jpg", "jpeg"],
    )

    if st.sidebar.button("Use an existing example"):
        # Choose a random sample image.
        st.sidebar.write("Click again to try out a different image.")
        random_index = random.randint(0, len(sample_images) - 1)
        img = Image.open(sample_images[random_index])
        img_mask = Image.open(sample_images_mask[random_index])
    else:
        if uploaded_img_file is not None:
            img = Image.open(io.BytesIO(uploaded_img_file.getvalue()))
            img = img.convert('RGB')

        if uploaded_img_mask is not None:
            img_mask = Image.open(io.BytesIO(uploaded_img_mask.getvalue()))
            img_mask = img_mask.convert('RGB')

        if img and img_mask and img.size != img_mask.size:
            st.warning(
                f"Image mask of size {img_mask.size} does not macth input img size {img.size}. Please upload mask that is the same size as image."
            )
            st.stop()

    if img is None and img_mask:
        st.warning("Image mask only works with an uploaded image. Please upload an image using the sidebar or use an existing example.")
        st.stop()
    elif img is None:
        st.warning("Please use the sidebar to upload an image or use an exisiting example. The sidebar can be expanded from the top left corner.")
        st.stop()

    # Conver PIL image to array.
    img = np.asarray(img, dtype=np.uint8)

    # Check that img and img_mask are rgb images.
    if img is not None and img.shape[2] !=3:
        st.warning(f"Image has to be an RGB image. Uploaded image has {img.shape[2]} channels. Please upload another image or use an existing example.")
        st.stop()

    if img_mask:
        img_mask = np.asarray(img_mask, dtype=np.uint8)
        if img_mask.shape[2] !=3:
            st.warning(f"Image mask has to be an RGB image. Uploaded image has {img.shape[2]} channels. If you have a binary image please concatenate the channels to make it RGB.")
            st.stop()

    # Show the images in sidebar.
    st.sidebar.write("Image loaded.")
    if img is not None:
        st.sidebar.image(img)
    if img_mask is not None:
        st.sidebar.image(img_mask)

    return img, img_mask

def get_sound(col1, col2):
    samplerate = 48000
    # Num Channels.
    num_channels = 1
    webrtc_ctx = webrtc_streamer(
        key="visualize-sound",
        mode=WebRtcMode.SENDONLY,
        audio_receiver_size=samplerate,
        rtc_configuration=RTC_CONFIGURATION,
        media_stream_constraints={
            "video": False,
            "audio": True
        },
        async_processing=True,
    )

    if not webrtc_ctx.state.playing:
        return

    # Get image arrays from user.
    img, img_mask = load_image()
    resized_img, img_foreground, img_background = process_image(img, img_mask)

    status_indicator = st.empty()
    status_indicator.write("Running. Say something!")

    with col1:
        st.image(resized_img)
    with col2:
        encoded_image_st = st.empty()

    fig_st = st.empty()
    while True:
        try:
            audio_frames = webrtc_ctx.audio_receiver.get_frames(timeout=1)
        except:
            status_indicator.write(
                "No frame arrived. Please check audio permissions and refresh."
            )
            return

        sound = pydub.AudioSegment.empty()
        for audio_frame in audio_frames:
            sound += pydub.AudioSegment(
                data=audio_frame.to_ndarray().tobytes(),
                sample_width=audio_frame.format.bytes,
                frame_rate=audio_frame.sample_rate,
                channels=num_channels,
            )
        # Dividing the ampltide by 10000 to get vaues in range [-1, 1]
        sound_array = np.array(sound.get_array_of_samples()) / 10000

        encoded_image_st.image(
            encode_image(sound_array, img_foreground, img_background))

        times = range(len(sound_array))
        source = pd.DataFrame({'Amplitude': sound_array, 'Time': times})

        fig_st.altair_chart(alt.Chart(source).mark_line().encode(
            alt.Y("Amplitude", scale=alt.Scale(domain=(-1.5, 1.5))),
            alt.X("Time", axis=None)),
                            use_container_width=True)


def main():
    selected_box = st.sidebar.selectbox('Choose one of the following', (
        'Welcome',
        'Visualize Sound in Real Time',
        'Visualize Sound - YouTube',
    ))

    if selected_box == 'Welcome':
        welcome()
    elif selected_box == 'Visualize Sound in Real Time':
        visualize_sound()
    elif selected_box == 'Visualize Sound - YouTube':
        visualize_youtube_video()

@st.cache(suppress_st_warning=True, ttl= 120)
def load_audio_from_link(link):
    try:
        yt=YouTube(link)
    except VideoUnavailable as e:
        return e
    strm=yt.streams.filter(only_audio=True, file_extension='mp4').first()
    if strm is None:
        return ValueError("Unable to load link.")

    # Reset the buffer and get the audio.
    buff = io.BytesIO()
    strm.stream_to_buffer(buff)
    buff.seek(0)
    full_audio = pydub.AudioSegment.from_file(buff)
    mono_audio = full_audio.split_to_mono()[0]
    return mono_audio


def get_youtube_link():
    yt_audios = {"Imagine Dragons: Believer" : "https://www.youtube.com/watch?v=Roi4TG6ZvKk", "You are my Sunshine" :"https://www.youtube.com/watch?v=dh7LJDHFaqA", "Doobey" :"https://www.youtube.com/watch?v=6eGCi4SVy94","Lindsey Stirling - Crystallize":"https://www.youtube.com/watch?v=aHjpOzsQ9YI"}
    select_box_link = st.selectbox("Choose a song or use your own link.", yt_audios.keys())
    link = st.text_input('YouTube Link', yt_audios[select_box_link])
    st.write(f"Using YouTube link: {link}.")
    return link

def visualize_youtube_video():
    st.header("Visualizing Sound !!!")
    st.markdown("""A first of its kind visualization of sound on an image.""")
    link = get_youtube_link()
    try:
        audio = load_audio_from_link(link)
    except:
        st.warning("The video is unavailable please try a different link.")
        st.stop()

    # Get the time span of the audio and set the range selection sliders.
    max_time_s = 10
    durations_seconds = int(audio.duration_seconds)
    start_time, end_time = st.select_slider(
     f'Woahh found {durations_seconds} seconds of audio!!! Please select a time interval within {max_time_s} s.',
     options=range(durations_seconds),
     value=(0, max_time_s))
    audio_time = end_time - start_time
    if audio_time < 0 or audio_time > max_time_s:
        st.warning(f"Please reduce the selected range to less than {max_time_s}s.")
        st.stop()

    # Set the frame rate for the video.
    fps = st.radio("Available frame rates (frames/second) for rendering the video.",(30, 60, 120, 240), index=1)

    # Get image arrays from user.
    img, img_mask = load_image()
    resized_img, img_foreground, img_background = process_image(img, img_mask)

    # Cut the aduio to the specified range.
    cut_audio = audio[start_time*1000:end_time*1000]
    st.write("Give the audio a listen while we get the visualization ready...")
    st.write(cut_audio)

    # Temp file for writing the final video.
    with tempfile.NamedTemporaryFile("w+b", suffix=".mp4") as video_writer:
        with st.spinner('Encoding Sound in the image...'):
            # Update images using FPS:
            chunk_ms = (1/fps) * 1000
            chunks = pydub.utils.make_chunks(cut_audio,chunk_ms)

            # Get Image Clips
            img_clips = []
            for chunk in chunks:
                # Dividing the ampltide by 10000 to get vaues in range [-1, 1]
                sound_array = np.array(chunk.get_array_of_samples()) / 10000
                img = encode_image(sound_array, img_foreground, img_background, True)
                img_clips.append(ImageClip(img).set_duration(1/fps))

            # Create video reader from moviepy
            # Export the current audio clip to binary file.
            with tempfile.NamedTemporaryFile("w+b", suffix=".wav") as audio_writer:
                cut_audio.export(audio_writer.name, "wav")
                audio_clip = AudioFileClip(audio_writer.name)
                video_clip = concatenate_videoclips(img_clips, method="compose")
                video_clip_with_audio =  video_clip.set_audio(audio_clip)
                video_clip_with_audio.write_videofile(video_writer.name, fps=fps,threads=multiprocessing.cpu_count())

        # Show the images
        col1, col2 = st.columns(2)
        with col1:
            st.image(resized_img)
        with col2:
            st.video(video_writer.name)

    st.write("Here is the entire audio for you to downlaod.")
    st.write(audio)


def visualize_sound():
    st.header("Visualizing Sound !!!")
    st.markdown("""A first of its kind visualization of sound on an image.""")

    # Plot sound and see its effects on an image.
    # Show the original image, image with effects and sound plot.
    col1, col2 = st.columns(2)
    get_sound(col1, col2)


def welcome():

    st.title("Visualizing Sound !!!")
    st.subheader('A simple app that lets you visualize sound in an image.')

    # Play an example !!!
    st.subheader('A day in the forest...')
    video_file = open('files/tree_with_sound.mp4', 'rb')
    video_bytes = video_file.read()
    st.video(video_bytes)


if __name__ == "__main__":
    import os

    DEBUG = os.environ.get("DEBUG",
                           "false").lower() not in ["false", "no", "0"]

    logging.basicConfig(
        format=
        "[%(asctime)s] %(levelname)7s from %(name)s in %(pathname)s:%(lineno)d: "
        "%(message)s",
        force=True,
    )

    logger.setLevel(level=logging.DEBUG if DEBUG else logging.INFO)

    st_webrtc_logger = logging.getLogger("streamlit_webrtc")
    st_webrtc_logger.setLevel(logging.DEBUG)

    fsevents_logger = logging.getLogger("fsevents")
    fsevents_logger.setLevel(logging.WARNING)

    main()
