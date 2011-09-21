#!/usr/bin/python

import wx
import math
import threading

# for app demo feature
import time
import random

# for real xbee feature
import xbee
import serial

# for results plotting
import cairoplot
from series import Series

# set to non-zero to show each incoming xbee frame to stdout
XVIZ_DEBUG = 0

# Xbee-related globals. All changes related to hardware setup should go here
ACCELEROMETER_MV_PER_G = 660.0
ACCELEROMETER_MAX_XYZ = 2.0
ACCELEROMETER_MIN_XYZ = -2.0
ACCELEROMETER_OFFSET = 1650.0
ACCELEROMETER_DIVIDER = 1.0
XBEE_PORT = "/dev/ttyUSB0" # you can find this using the dmesg command as root
XBEE_POLLING_FREQUENCY = 50
XBEE_VREF = 3300
DISPLAY_TIME_SECONDS = 3

# GUI-related globals
USE_BUFFERED_DC = True
UPDATE_EVENT = wx.NewId() #some ID number to use when new data has been collected on serial port from Xbee
START_BUTTON = wx.NewId() #the start button
STOP_BUTTON = wx.NewId() #the stop button
QUIT_BUTTON = wx.NewId() #to quit the program
X_AXIS_COLOR = (16, 225, 53)
Y_AXIS_COLOR = (0, 174, 253)
Z_AXIS_COLOR = (255, 128, 0)
LANGUAGE = "en"


# some basic integrated internationalization
lang = {}
lang["en"] = {"osd_x_label" : "X axis",
              "osd_y_label" : "Y axis",
              "osd_z_label" : "Z axis",
              "osd_status_init" : "Application initializing...",
              "osd_status_record" : "Recording to memory",
              "osd_status_save" : "Saving CSV to disk",
              "osd_status_plot" : "Plotting XYZ series",
              "osd_status_live" : "Showing live sensor data",
              "osd_button_start" : "Start",
              "osd_button_stop" : "Stop",
              "osd_button_quit" : "Quit...",
              "csv_header" : "#timestamp (seconds), X-axis (g), Y-axis (g), Z-axis (g)\n",
              "png_label" : "%s axis",
              "png_x_label" : "Running time (s)",
              "png_y_label" : "Acceleration (g)",
              "osd_title" : "Xbee accelerometer monitor"
              }

lang["fr"] = {"osd_x_label" : "axe X",
              "osd_y_label" : "axe Y",
              "osd_z_label" : "axe Z",
              "osd_status_init" : "Initialisation en cours...",
              "osd_status_record" : "Acquisition en memoire",
              "osd_status_save" : "Sauvegarde du fichier CSV",
              "osd_status_plot" : "Tracage des courbes XYZ",
              "osd_status_live" : "Affichage en temps reel",
              "osd_button_start" : "Demarrer",
              "osd_button_stop" : "Arreter",
              "osd_button_quit" : "Quitter...",
              "csv_header" : "#temps ecoule (secondes), axe X (g), axe Y (g), axe Z (g)\n",
              "png_label" : "axe %s",
              "png_x_label" : "Temps ecoule (s)",
              "png_y_label" : "Acceleration (g)",
              "osd_title" : "Moniteur Xbee pour accelerometre"
              }

class AccelDataEvent(wx.PyEvent):
    """Event that delivers XYZ Data as received by Xbee"""
    def __init__(self, data):
        wx.PyEvent.__init__(self)
        self.SetEventType(UPDATE_EVENT)
        self.data = data

class PollThread(threading.Thread):
    """Base class that abstracts polling dynamics"""
    def __init__(self, target_window):
        threading.Thread.__init__(self)
        self._target = target_window
        # capture synchronization with GUI thread
        self._should_quit = False

    def run(self):
        self.poll_init()

        while not self._should_quit:
            self.poll_once()

        self.poll_cleanup()
        
    def stop(self):
        self._should_quit = True

    #empty methods to be overridden by subclasses
    def poll_init(self):
        """override with code to run once, before polling starts"""
        pass

    def poll_once(self):
        """override with capture logic, should be blocking"""
        pass

    def poll_cleanup(self):
        """override with capture cleanup code to run when capture is done"""
        pass
        

class FakePollThread(PollThread):
    """Thread that emulates polling the Xbee device, to demonstrate python app,
    use it when you want to test the application and have no xbee at handy."""
    def __init__(self, target_window):
        PollThread.__init__(self, target_window)

    def poll_init(self):
        self._x = 0.0
        self._y = 0.0
        self._z = 0.0

    def poll_once(self):

        def clamp(v, _min, _max):
            if v < _min:
                return _min
            elif v > _max:
                return _max
            return v

        wx.PostEvent(self._target, AccelDataEvent((self._x, self._y, self._z)))
        self._x = clamp(self._x + ((random.randint(0, 10) - 5) / 500.0) * (ACCELEROMETER_MAX_XYZ-ACCELEROMETER_MIN_XYZ), ACCELEROMETER_MIN_XYZ, ACCELEROMETER_MAX_XYZ)
        self._y = clamp(self._y + ((random.randint(0, 10) - 5) / 500.0) * (ACCELEROMETER_MAX_XYZ-ACCELEROMETER_MIN_XYZ), ACCELEROMETER_MIN_XYZ, ACCELEROMETER_MAX_XYZ)
        self._z = clamp(self._z + ((random.randint(0, 10) - 5) / 500.0) * (ACCELEROMETER_MAX_XYZ-ACCELEROMETER_MIN_XYZ), ACCELEROMETER_MIN_XYZ, ACCELEROMETER_MAX_XYZ)

        time.sleep(0.02)

    def poll_cleanup(self):
        pass

class XbeePollThread(PollThread):
    """Thread that polls the Xbee device on serial/USB port"""
    def __init__(self, target_window, comport, divider, mVperG, offset, vref):
        PollThread.__init__(self, target_window)

        # Xbee specifics
        self._comport = comport
        self._divider = float(divider)
        self._mVperG = float(mVperG)
        self._offset = float(offset)
        self._vref = float(vref)

        # data lines
        self._x = 0.0
        self._y = 0.0
        self._z = 0.0

    def poll_init(self):
        self._port = serial.Serial(self._comport, 9600)
        self._bee = xbee.XBee(self._port)

    def poll_once(self):

        def adc_to_accel(adc):
            return ((self._divider * (adc * self._vref / 1024.0)) - self._offset) / self._mVperG

        frame = self._bee.wait_read_frame()
        samples = frame["samples"][0] # we only asked for 1 sample per packet
        _x = adc_to_accel(samples["adc-0"])
        _y = adc_to_accel(samples["adc-1"])
        _z = adc_to_accel(samples["adc-2"])
#        print (_x, _y, _z)
        if XVIZ_DEBUG:
            print frame
        wx.PostEvent(self._target, AccelDataEvent((_x, _y, _z)))

    def poll_cleanup(self):
        self._port.close()

class BufferedWindow(wx.Window):
    """This seems to be missing from my current version of wxpython. 
    Or maybe I got it wrong and it is not supposed to be part of wxpython at all ?
    Stole it here : http://wiki.wxpython.org/DoubleBufferedDrawing"""
    def __init__(self, *args, **kwargs):
        # make sure the NO_FULL_REPAINT_ON_RESIZE style flag is set.
        kwargs['style'] = kwargs.setdefault('style', wx.NO_FULL_REPAINT_ON_RESIZE) | wx.NO_FULL_REPAINT_ON_RESIZE
        wx.Window.__init__(self, *args, **kwargs)

        wx.EVT_PAINT(self, self.OnPaint)
        wx.EVT_SIZE(self, self.OnSize)

        # OnSize called to make sure the buffer is initialized.
        # This might result in OnSize getting called twice on some
        # platforms at initialization, but little harm done.
        self.OnSize(None)

    def Draw(self, dc):
        pass

    def OnSize(self, event):
        # The Buffer init is done here, to make sure the buffer is always
        # the same size as the Window
        Size = self.ClientSize

        # Make new offscreen bitmap: this bitmap will always have the
        # current drawing in it, so it can be used to save the image to
        # a file, or whatever.
        self._Buffer = wx.EmptyBitmap(*Size)
        self.UpdateDrawing()    

    def OnPaint(self, event):
        # All that is needed here is to draw the buffer to screen
        if USE_BUFFERED_DC:
            dc = wx.BufferedPaintDC(self, self._Buffer)
        else:
            dc = wx.PaintDC(self)
            dc.DrawBitmap(self._Buffer, 0, 0)

    def UpdateDrawing(self):
        dc = wx.MemoryDC()
        dc.SelectObject(self._Buffer)
        self.Draw(dc)
        del dc # need to get rid of the MemoryDC before Update() is called.
        self.Refresh()
        self.Update()

class graphWindow(BufferedWindow):
    """The window that displays the curve for a set of points,
    and subclasses our integrated version of a double-buffered window.
    It has some limited support for auto-range on the Y axis, while
    it was ditched on the X axis and replaced by auto-scroll.

    In order to be able to work with auto-range, Y range bounds must
    be on each side of the X axis."""
    def __init__(self, *args, **kwargs):
        # init with some safe defaults, these should be overwritten using methods
        self.points = []
        self.xrange = (0, 100)
        self.yrange = (-100, 100)
        self.color = (255, 0, 0)
        self.bgcolor = (35, 35, 35)

        BufferedWindow.__init__(self, *args, **kwargs)

    def _add_point(self, _value):
        """private method that does the adding of the point to the data list
        and computes the new Y range if needed"""
        _, fullrange = self.xrange
        cutrange = fullrange / 10
        if len(self.points) >= fullrange:
            self.points = self.points[fullrange-cutrange:]
        self.points.append(_value)

        #update y range
        _min, _max = self.yrange

        while _value > _max:
            _max = 2 * _max
        while _value < _min:
            _min = 2 * _min # this is assuming that _min is negative at all times. TODO: better auto-range
        self.yrange = (_min, _max)

    def add_point(self, value):
        """Adds a value to the current dataset. 
        Y range will be taken care of automatically,
        if the new value is out of bounds.

        Also, this method asks for a redraw of the
        window from the main GUI thread, because it
        might be called from the polling thread."""
        self._add_point(value)
        wx.CallAfter(self.UpdateDrawing) # make sure these are called from the GUI thread

    def set_color(self, r, g, b):
        """Change the  value of the color used to plot the data.
        
        Pass three 8-bit based values for r, g, b in that order."""
        self.color = (r, g, b)

    def set_value_range(self, _min, _max):
        """Defines the initial range for measured values.

        Note that the value recording function might
        grow this range when needed.
        
        The unit is consistent with measured values
        as sent by the Xbee (i.e. it depends on the
        sensor, the sensor parameters, and ultimately
        on the value for vref coming through pin 14)"""
        self.yrange = (_min, _max)
        
    def set_time_range(self, _max):
        """Defines the time range over which the
        values are shown.

        The unit is number of samples"""
        self.xrange = (0, _max)
        
    def Draw(self, dc):
        """Main drawing routine called by the 
        DoubleBufferedWindow super-class"""
        # comput scaling factors
        _w, _h = self.GetClientSize()
        _xmin, _xmax = self.xrange
        _ymin, _ymax = self.yrange
        xscale = _w / float(_xmax - _xmin)
        yscale = _h / float(_ymax - _ymin)

        # clear viewport
        dc.SetBackground(wx.Brush(self.bgcolor, wx.SOLID))
        dc.Clear()
        
        # set drawing color for axis
        dc.SetBrush(wx.Brush(self.color, wx.SOLID))
        dc.SetPen(wx.Pen((255,255,255), 1, wx.SOLID))

        # scaling helper functions
        _xscale = lambda x: int((x-_xmin) * xscale)
        _yscale = lambda y: int(_h - (y-_ymin) * yscale)

        #draw axis
        if _xmin <= 0 and _xmax >= 0:
            dc.DrawLine(-_xmin * _xscale(0), 0, _xscale(0), _h)
        if _ymin <= 0 and _ymax >= 0:
            dc.DrawLine(0, _yscale(0), _w, _yscale(0))

        # set drawing color for plot
        dc.SetBrush(wx.Brush(self.color, wx.SOLID))
        dc.SetPen(wx.Pen(self.color, 1, wx.SOLID))

        def time_range():
            _min, _max = self.xrange
            return _max - _min

        #plot lines after scaling according to window size and value ranges
        if len(self.points) > 0:
            scaledlines = []
            for i in range(1,len(self.points)):
                scaledlines.append((_xscale(i-1), _yscale(self.points[i-1]), _xscale(i), _yscale(self.points[i])))
            dc.DrawLineList(scaledlines)

def register_event(window, function, event):
    window.Connect(-1, -1, event, function)

class xvizFrame(wx.Frame):
    """The main graphical frame of the Xbee application"""
    def __init__(self, parent, id_, title):
        wx.Frame.__init__(self, parent, id_, title)

        panel = wx.Panel(self, -1)

        # recording logic
        self.recording = False
        self.recorder = []
        self.record_start = 0

        self.xScreen = graphWindow(parent=panel, id=-1, size=(200,200))
        self.xScreen.set_color(*X_AXIS_COLOR)
        self.xScreen.set_value_range(ACCELEROMETER_MIN_XYZ, ACCELEROMETER_MAX_XYZ)
        self.xScreen.set_time_range(XBEE_POLLING_FREQUENCY * DISPLAY_TIME_SECONDS)

        self.yScreen = graphWindow(parent=panel, id=-1, size=(200,200))
        self.yScreen.set_color(*Y_AXIS_COLOR)
        self.yScreen.set_value_range(ACCELEROMETER_MIN_XYZ, ACCELEROMETER_MAX_XYZ)
        self.yScreen.set_time_range(XBEE_POLLING_FREQUENCY * DISPLAY_TIME_SECONDS)

        self.zScreen = graphWindow(parent=panel, id=-1, size=(200,200))
        self.zScreen.set_color(*Z_AXIS_COLOR)
        self.zScreen.set_value_range(ACCELEROMETER_MIN_XYZ, ACCELEROMETER_MAX_XYZ)
        self.zScreen.set_time_range(XBEE_POLLING_FREQUENCY * DISPLAY_TIME_SECONDS)

        xlabel = wx.StaticText(panel, -1, lang[LANGUAGE]["osd_x_label"])
        ylabel = wx.StaticText(panel, -1, lang[LANGUAGE]["osd_y_label"])
        zlabel = wx.StaticText(panel, -1, lang[LANGUAGE]["osd_z_label"])

        self.status_text = wx.StaticText(panel, -1, lang[LANGUAGE]["osd_status_init"])
        self.status = 0

        start = wx.Button(panel, START_BUTTON, lang[LANGUAGE]["osd_button_start"])
        stop = wx.Button(panel, STOP_BUTTON, lang[LANGUAGE]["osd_button_stop"])
        quit_ = wx.Button(panel, QUIT_BUTTON, lang[LANGUAGE]["osd_button_quit"])

        hsz1 = wx.BoxSizer(wx.HORIZONTAL)
        hsz1.Add(self.xScreen, 1, wx.ALL | wx.EXPAND, 5)
        hsz1.Add(self.yScreen, 1, wx.ALL | wx.EXPAND, 5)
        hsz1.Add(self.zScreen, 1, wx.ALL | wx.EXPAND, 5)
        
        hsz2 = wx.BoxSizer(wx.HORIZONTAL)
        hsz2.Add(xlabel, 1, wx.ALL | wx.EXPAND, 5)
        hsz2.Add(ylabel, 1, wx.ALL | wx.EXPAND, 5)
        hsz2.Add(zlabel, 1, wx.ALL | wx.EXPAND, 5)

        hsz3 = wx.BoxSizer(wx.HORIZONTAL)
        hsz3.Add(start, 1, wx.ALL | wx.EXPAND, 5)
        hsz3.Add(stop, 1, wx.ALL | wx.EXPAND, 5)
        hsz3.Add(quit_, 1, wx.ALL | wx.EXPAND, 5)

        hsz4 = wx.BoxSizer(wx.HORIZONTAL)
        hsz4.Add(self.status_text, wx.ALL | wx.EXPAND, 5)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(hsz1, 1, wx.EXPAND, 0)
        sizer.Add(hsz2, 0, wx.EXPAND, 0)
        sizer.Add(hsz3, 0, wx.EXPAND, 0)
        sizer.Add(hsz4, 0, wx.EXPAND, 0)
        panel.SetSizerAndFit(sizer)

        self.poller = None
        register_event(self, self.onNewData, UPDATE_EVENT)

        self.Bind(wx.EVT_BUTTON, self.onStartBtn, id = START_BUTTON)
        self.Bind(wx.EVT_BUTTON, self.onStopBtn, id = STOP_BUTTON)
        self.Bind(wx.EVT_BUTTON, self.onQuitBtn, id = QUIT_BUTTON)

        self.Bind(wx.EVT_CLOSE, self.onClose)

        self.panel = panel
        self.Fit()

        self.createPoller()
        self.poller.start()

    def createPoller(self):
        """Creates the poller that will query the Xbee on serial/USB port.
        Alternatively, you can modify this method to instantiate a
        fake poll thread instead that returns delta-randomized values"""
        self.poller = XbeePollThread(self, XBEE_PORT, ACCELEROMETER_DIVIDER, ACCELEROMETER_MV_PER_G, ACCELEROMETER_OFFSET, XBEE_VREF)
#        self.poller = FakePollThread(self)

    def onStartBtn(self, event):
        """Handle user input : GUI start button"""
        if not self.recording:
            self.record_start = time.time()
            self.status_text.SetLabel(lang[LANGUAGE]["osd_status_record"])
        self.recording = True

    def onStopBtn(self, event):
        """Handle user input : GUI stop button"""
        if self.recording:
            self.recording = False

            self.status_text.SetLabel(lang[LANGUAGE]["osd_status_save"])
            self.save_curves()

            self.status_text.SetLabel(lang[LANGUAGE]["osd_status_plot"])
            self.plot_curves()

            self.record_start = 0
            self.recorder = []

            self.status_text.SetLabel(lang[LANGUAGE]["osd_status_live"])

    def onClose(self, event):
        self.poller.stop()
        self.poller.join()
        self.app.Exit()

    def onQuitBtn(self, event):
        """Handle user input : GUI quit button"""
        self.Close()

    def onNewData(self, event):
        """Handle data input : new values were received by polling thread"""
        timestamp = time.time()
        (_x, _y, _z) = event.data

        if not self.status: # on first value packet, change "initializing" text
            self.status = 1
            self.status_text.SetLabel(lang[LANGUAGE]["osd_status_live"])

        if self.recording: # record values, if user so chose
            self.recorder.append((timestamp, _x, _y, _z))

        self.xScreen.add_point(_x)
        self.yScreen.add_point(_y)
        self.zScreen.add_point(_z)

    def save_curves(self):
        """Helper method to export recorded data in CSV form
        for use in your favorite CSV number cruncher"""
        start_time = self.record_start
        st = time.localtime(start_time)
        filename = "accelerometer-log-%d_%02d_%02d-%02d_%02d_%02d.csv" % (st.tm_year, st.tm_mon, st.tm_mday, st.tm_hour, st.tm_min, st.tm_sec)
        output = open(filename, "wb")
        output.write(lang[LANGUAGE]["csv_header"])
        for (t, x, y, z) in self.recorder:
            output.write("%f\t%f\t%f\t%f\n" % (t-start_time, x, y, z))
        output.close()

    def _plot_data(self, graphique):
        """Helper method to do the graphic rendering and write it to disk"""
        graphique.render()
        graphique.commit()

    def plot_data_single(self, basename, series_name, format_, data, color):
        """Helper method to save a single axis' data to a graphic file"""
        surface = "%s-%s.%s" % (basename, series_name, format_)
        data[0].name = lang[LANGUAGE]["png_label"] % series_name
        graphique = cairoplot.DotLinePlot(surface, data,
                                          640, 480,
                                          "white light_gray", 15,
                                          axis = True,
                                          x_title = lang[LANGUAGE]["png_x_label"],
                                          y_title = lang[LANGUAGE]["png_y_label"],
                                          y_bounds = (ACCELEROMETER_MIN_XYZ,
                                                      ACCELEROMETER_MAX_XYZ),
                                          series_legend = True,
                                          series_colors = color)
        self._plot_data(graphique)

    def plot_data_all(self, basename, format_, data, color):
        """Helper method to save all axis' data to a graphic file"""
        surface = "%s.%s" % (basename, format_)
        data[0].name = lang[LANGUAGE]["png_label"] % "X"
        data[1].name = lang[LANGUAGE]["png_label"] % "Y"
        data[2].name = lang[LANGUAGE]["png_label"] % "Z"
        graphique = cairoplot.DotLinePlot(surface, data,
                                          640, 480,
                                          "white light_gray", 15,
                                          axis = True,
                                          x_title = lang[LANGUAGE]["png_x_label"],
                                          y_title = lang[LANGUAGE]["png_y_label"],
                                          y_bounds = (ACCELEROMETER_MIN_XYZ,
                                                      ACCELEROMETER_MAX_XYZ),
                                          series_legend = True,
                                          series_colors = color)
        self._plot_data(graphique)

    def plot_curves(self):
        """Helper method to save the overall curve as well as per-axis curve"""
        start_time = self.record_start
        end_time, _, _, _ = self.recorder[-1]
        st = time.localtime(start_time)
        basename = "accelerometer-plot-%d_%02d_%02d-%02d_%02d_%02d" % (st.tm_year, st.tm_mon, st.tm_mday, st.tm_hour, st.tm_min, st.tm_sec)


        datax = []
        datay = []
        dataz = []
        for (t, x, y, z) in self.recorder:
            deltat = t - start_time
            datax.append((deltat,x))
            datay.append((deltat,y))
            dataz.append((deltat,z))
        data = Series([datax, datay, dataz])

        def _map_color(col):
            return map(lambda c: c / 255.0, col)

        x_color = _map_color(X_AXIS_COLOR)
        y_color = _map_color(Y_AXIS_COLOR)
        z_color = _map_color(Z_AXIS_COLOR)

        self.plot_data_all(basename, "png", data, [x_color, y_color, z_color])
        self.plot_data_single(basename, "X", "png", Series([datax]), [x_color])
        self.plot_data_single(basename, "Y", "png", Series([datay]), [y_color])
        self.plot_data_single(basename, "Z", "png", Series([dataz]), [z_color])
        
class xvizApp(wx.App):
    """The application class that does nothing but creating a wxFrame
    and bringing it on the top..."""
    def OnInit(self):
        frame = xvizFrame(None, -1, lang[LANGUAGE]["osd_title"])
        # I don't know about wxwidgets/wxpython details, but calling Exit
        # on the wx.App object itself got rid of a few glib-gobject annoying
        # warnings about 'no handler with id blah' that were showing up
        # when trying to quit by calling Destroy on the frame itself...
        frame.app = self
        frame.Show(True)

        self.SetTopWindow(frame)
        
        return True

app = xvizApp(0)
app.MainLoop() # start the main loop
