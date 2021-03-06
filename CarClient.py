from kivy.app import App
from kivy.clock import Clock
from kivy.graphics.texture import Texture
from kivy.core.window import Window
import sys
import cv2
import numpy as np
import socket
import configparser
from kivy.uix.widget import Widget
from kivy.properties import StringProperty, ObjectProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.button import Button
from kivy.uix.scrollview import ScrollView
import threading
import glob
import pyaudio
import wave
import inputs

sound_stream = None

class SoundStream(threading.Thread):
    def __init__(self, wav_filename):
        config = configparser.ConfigParser()
        config.read('./settings.ini', 'UTF-8')
        self.SERVER_IP = App.get_running_app().SERVER_IP
        self.SERVER_PORT = int(config.get('sound', 'port'))
        self.FORMAT = pyaudio.paInt16
        self.CHANNELS = int(config.get('sound', 'channels'))
        self.RATE = int(config.get('sound', 'rate'))
        self.CHUNK = int(config.get('sound', 'chunk'))
        threading.Thread.__init__(self)
        self.voice_volume = 0.0
        self.music_volume = 1.0
        self.load_audio(wav_filename)

    def run(self):
        audio = pyaudio.PyAudio()
        stream = audio.open(format=self.FORMAT, channels=self.CHANNELS, rate=self.RATE, input=True, frames_per_buffer=self.CHUNK)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect((self.SERVER_IP, self.SERVER_PORT))

            while True:
                voice_data = stream.read(self.CHUNK)
                music_data = self.wav_file.readframes(self.CHUNK)
                if music_data == b'':
                    self.wav_file.rewind()
                    music_data = self.wav_file.readframes(self.CHUNK)
                sock.send(self.mix_sounds(voice_data, music_data, self.CHANNELS, self.CHUNK))

        stream.stop_stream()
        stream.close()
        audio.terminate()

    def mix_sounds(self, data1, data2, channels, chunk):
        decoded_data1 = np.frombuffer(data1, np.int16).copy()
        decoded_data2 = np.frombuffer(data2, np.int16).copy()
        decoded_data1.resize(channels * chunk, refcheck=False)
        decoded_data2.resize(channels * chunk, refcheck=False)
        return (decoded_data1 * self.voice_volume + decoded_data2 * self.music_volume).astype(np.int16).tobytes()

    def change_volumes(self, volume1, volume2):
        self.voice_volume = volume1
        self.music_volume = volume2
    
    def load_audio(self, wav_filename):
        self.wav_file = wave.open(wav_filename, 'rb')

class GamepadController(threading.Thread):
        def __init__(self):
            config = configparser.ConfigParser()
            config.read('./settings.ini')
            self.SERVER_IP = App.get_running_app().SERVER_IP
            self.SERVER_PORT = int(config.get('gamepad', 'port'))
            self.COORDINATE_MAX = 32767
            self.PARTITION_NUMBER = 4
            threading.Thread.__init__(self)

        def run(self):
            global sound_stream

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((self.SERVER_IP, self.SERVER_PORT))

                prev_y = 0
                prev_x = 0
                operable_list = ["ABS_Y", "ABS_RX", "BTN_NORTH", "BTN_WEST", "BTN_SOUTH", "BTN_EAST", "BTN_TR"]

                while True:
                    events = inputs.get_gamepad()
                    for event in events:
                        if  event.code in operable_list:
                            button_type = event.code
                            button_val = event.state

                            if event.code == 'BTN_TR':
                                if event.state == 0:
                                    sound_stream.change_volumes(0.0, 1.0)
                                elif event.state == 1:
                                    sound_stream.change_volumes(1.0, 0.0)

                            if event.code in ['ABS_Y', 'ABS_RX']:
                                button_val = int(max(event.state, -self.COORDINATE_MAX) / (self.COORDINATE_MAX / self.PARTITION_NUMBER))
                                
                                if event.code == 'ABS_Y':
                                    if button_val == prev_y:
                                        continue
                                    prev_y = button_val
                                
                                if event.code == 'ABS_RX':
                                    if button_val == prev_x:
                                        continue
                                    prev_x = button_val

                            sock.send((button_type + ' ' + str(button_val) + ',').encode('utf-8'))

class RootWidget(BoxLayout):
    def __init__(self, **kwargs):
        super(RootWidget, self).__init__(**kwargs)

class WebCameraWidget(Widget):

    web_camera_image = ObjectProperty(None)

    def __init__(self, **kwargs):
        super(WebCameraWidget, self).__init__(**kwargs)

        config = configparser.ConfigParser()
        config.read('./settings.ini', 'UTF-8')

        # 通信用設定
        self.buff = bytes()
        self.soc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.SERVER_IP = App.get_running_app().SERVER_IP
        self.SERVER_PORT = int(config.get('web_camera', 'port'))
        self.PACKET_HEADER_SIZE = int(config.get('web_camera', 'header_size'))
        self.IMAGE_WIDTH = int(config.get('web_camera', 'image_width'))
        self.IMAGE_HEIGHT = int(config.get('web_camera', 'image_height'))

        # 表示設定
        self.VIEW_FPS = 30
        self.VIEW_WIDTH = 800
        self.VIEW_HEIGHT = 600

        # 画面更新メソッドの呼び出し設定
        Clock.schedule_interval(self.update, 1.0 / self.VIEW_FPS)

        # サーバに接続
        try:
            self.soc.connect((self.SERVER_IP, self.SERVER_PORT))
        except socket.error as e:
            print('Connection failed.')
            sys.exit(-1)

    def update(self, dt):
        # サーバからのデータをバッファに蓄積
        data = self.soc.recv(self.IMAGE_HEIGHT * self.IMAGE_WIDTH * 3)
        self.buff += data

        # 最新のパケットの先頭までシーク
        # バッファに溜まってるパケット全ての情報を取得
        packet_head = 0
        packets_info = list()
        while True:
            if len(self.buff) >= packet_head + self.PACKET_HEADER_SIZE:
                binary_size = int.from_bytes(self.buff[packet_head:packet_head + self.PACKET_HEADER_SIZE], 'big')
                if len(self.buff) >= packet_head + self.PACKET_HEADER_SIZE + binary_size:
                    packets_info.append((packet_head, binary_size))
                    packet_head += self.PACKET_HEADER_SIZE + binary_size
                else:
                    break
            else:
                break

        # バッファの中に完成したパケットがあれば、画像を更新
        if len(packets_info) > 0:
            # 最新の完成したパケットの情報を取得
            packet_head, binary_size = packets_info.pop()
            # パケットから画像のバイナリを取得
            img_bytes = self.buff[packet_head + self.PACKET_HEADER_SIZE:packet_head + self.PACKET_HEADER_SIZE + binary_size]
            # バッファから不要なバイナリを削除
            self.buff = self.buff[packet_head + self.PACKET_HEADER_SIZE + binary_size:]

            # 画像をバイナリから復元
            img = np.frombuffer(img_bytes, dtype=np.uint8)
            img = cv2.imdecode(img, 1)
            # 画像を表示用に加工
            img = cv2.flip(img, 0)
            img = cv2.resize(img, (self.VIEW_WIDTH, self.VIEW_HEIGHT))
            # 画像をバイナリに変換
            img = img.tostring()

            # 作成した画像をテクスチャに設定
            img_texture = Texture.create(size=(self.VIEW_WIDTH, self.VIEW_HEIGHT), colorfmt='bgr')
            img_texture.blit_buffer(img, colorfmt='bgr', bufferfmt='ubyte')
            #self.texture = img_texture
            self.web_camera_image.texture = img_texture

    def disconnect(self):
        # サーバとの接続を切断
        self.soc.shutdown(socket.SHUT_RDWR)
        self.soc.close()

class AudioListWidget(Widget):
    def __init__(self, **kwargs):
        super(AudioListWidget, self).__init__(**kwargs)

        layout = GridLayout(cols=1, spacing=0, size_hint_y=None)
        layout.bind(minimum_height=layout.setter('height'))
        audio_list = glob.glob("./*.wav")
        for audio_filename in audio_list:
            btn = Button(text=audio_filename, size_hint_y=None, height=40)
            btn.bind(on_press=self.audio_select)
            layout.add_widget(btn)

        scroll_view = ScrollView(size_hint=(None, None), size=(200, Window.height))
        scroll_view.add_widget(layout)
        scroll_view.pos = (800, 0)
        
        self.add_widget(scroll_view)

        self.sound_stream = SoundStream(audio_list[0])
        self.sound_stream.setDaemon(True)
        self.sound_stream.start()

        global sound_stream
        sound_stream = self.sound_stream
    
    def audio_select(self, instance):
        print('The button <%s> is being pressed' % instance.text)
        self.sound_stream.load_audio(instance.text)

class GamepadWidget(Widget):
    def __init__(self, **kwargs):
        super(GamepadWidget, self).__init__(**kwargs)

        self.gamepad_controller = GamepadController()
        self.gamepad_controller.setDaemon(True)
        self.gamepad_controller.start()

class CarClientApp(App):

    def __init__(self, window_width, window_height, server_ip, **kwargs):
        super(CarClientApp, self).__init__(**kwargs)
        self.WINDOW_WIDTH = window_width
        self.WINDOW_HEIGHT = window_height
        self.SERVER_IP = server_ip

    def build(self):
        Window.size = (self.WINDOW_WIDTH, self.WINDOW_HEIGHT)
        return RootWidget()

    def on_stop(self):
        pass

if __name__ == '__main__':
    cca = CarClientApp(window_width=1000, window_height=600, server_ip=sys.argv[1])
    cca.run()
