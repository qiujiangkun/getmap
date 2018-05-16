# coding=utf-8

"""
pygetmap:

Download web map by cooridinates

"""

# Longitude 经度
# Latitude   纬度
# Mecator x = y = [-20037508.3427892,20037508.3427892]
# Mecator Latitue = [-85.05112877980659，85.05112877980659]

import math
import os

import requests
import sys
from math import floor, pi, log, tan, atan, exp
from threading import Thread, Lock
from PIL import Image
import io
import traceback

MAP_URLS = {
    "google": "http://mt2.google.cn/vt/lyrs={style}&hl=zh-CN&gl=CN&src=app&x={x}&y={y}&z={z}",
    "amap": "http://wprd02.is.autonavi.com/appmaptile?style={style}&x={x}&y={y}&z={z}",
    "tencent_s": "http://p3.map.gtimg.com/sateTiles/{z}/{fx}/{fy}/{x}_{y}.jpg",
    "tencent_m": "http://rt0.map.gtimg.com/tile?z={z}&x={x}&y={y}&styleid=3"}

COUNT = 0
mutex = Lock()


# -----------------GCJ02到WGS84的纠偏与互转---------------------------
def transform_lat(x, y):
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def transform_lon(x, y):
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def delta(lat, lon):
    """
    Krasovsky 1940
    //
    // a = 6378245.0, 1/f = 298.3
    // b = a * (1 - f)
    // ee = (a^2 - b^2) / a^2;
    """
    a = 6378245.0  # a: 卫星椭球坐标投影到平面地图坐标系的投影因子。
    ee = 0.00669342162296594323  # ee: 椭球的偏心率。
    dLat = transform_lat(lon - 105.0, lat - 35.0)
    dLon = transform_lon(lon - 105.0, lat - 35.0)
    radLat = lat / 180.0 * math.pi
    magic = math.sin(radLat)
    magic = 1 - ee * magic * magic
    sqrtMagic = math.sqrt(magic)
    dLat = (dLat * 180.0) / ((a * (1 - ee)) / (magic * sqrtMagic) * math.pi)
    dLon = (dLon * 180.0) / (a / sqrtMagic * math.cos(radLat) * math.pi)
    return {'lat': dLat, 'lon': dLon}


def out_of_china(lat, lon):
    if lon < 72.004 or lon > 137.8347:
        return True
    if lat < 0.8293 or lat > 55.8271:
        return True
    return False


def gcj_to_wgs(gcjLon, gcjLat):
    if out_of_china(gcjLat, gcjLon):
        return (gcjLon, gcjLat)
    d = delta(gcjLat, gcjLon)
    return gcjLon - d["lon"], gcjLat - d["lat"]


def wgs_to_gcj(wgsLon, wgsLat):
    if out_of_china(wgsLat, wgsLon):
        return wgsLon, wgsLat
    d = delta(wgsLat, wgsLon)
    return wgsLon + d["lon"], wgsLat + d["lat"]


# --------------------------------------------------------------

# ------------------wgs84与web墨卡托互转-------------------------

# WGS-84经纬度转Web墨卡托
def wgs_to_macator(x, y):
    y = 85.0511287798 if y > 85.0511287798 else y
    y = -85.0511287798 if y < -85.0511287798 else y

    x2 = x * 20037508.34 / 180
    y2 = log(tan((90 + y) * pi / 360)) / (pi / 180)
    y2 = y2 * 20037508.34 / 180
    return x2, y2


# Web墨卡托转经纬度
def mecator_to_wgs(x, y):
    x2 = x / 20037508.34 * 180
    y2 = y / 20037508.34 * 180
    y2 = 180 / pi * (2 * atan(exp(y2 * pi / 180)) - pi / 2)
    return x2, y2


# -------------------------------------------------------------

# ---------------------------------------------------------
'''
东经为正，西经为负。北纬为正，南纬为负
j经度 w纬度 z缩放比例[0-22] ,对于卫星图并不能取到最大，测试值是20最大，再大会返回404.
山区卫星图可取的z更小，不同地图来源设置不同。
'''


# 根据WGS-84 的经纬度获取谷歌地图中的瓦片坐标
def wgs84_to_tile(j, w, z):
    """
    Get google-style tile cooridinate from geographical coordinate
    j : Longittude
    w : Latitude
    z : zoom
    """
    isnum = lambda x: isinstance(x, int) or isinstance(x, float)
    if not (isnum(j) and isnum(w)):
        raise TypeError("j and w must be int or float!")

    if not isinstance(z, int) or z < 0 or z > 22:
        raise TypeError("z must be int and between 0 to 22.")

    if j < 0:
        j = 180 + j
    else:
        j += 180
    j /= 360  # make j to (0,1)

    w = 85.0511287798 if w > 85.0511287798 else w
    w = -85.0511287798 if w < -85.0511287798 else w
    w = log(tan((90 + w) * pi / 360)) / (pi / 180)
    w /= 180  # make w to (-1,1)
    w = 1 - (w + 1) / 2  # make w to (0,1) and left top is 0-point

    num = 2 ** z
    x = floor(j * num)
    y = floor(w * num)
    return x, y


def tileframe_to_mecatorframe(zb):
    # 根据瓦片四角坐标，获得该区域四个角的web墨卡托投影坐标
    inx, iny = zb["LT"]  # left top
    inx2, iny2 = zb["RB"]  # right bottom
    length = 20037508.3427892
    sum = 2 ** zb["z"]
    LTx = inx / sum * length * 2 - length
    LTy = -(iny / sum * length * 2) + length

    RBx = (inx2 + 1) / sum * length * 2 - length
    RBy = -((iny2 + 1) / sum * length * 2) + length

    # LT=left top,RB=right buttom
    # 返回四个角的投影坐标
    res = {'LT': (LTx, LTy), 'RB': (RBx, RBy),
           'LB': (LTx, RBy), 'RT': (RBx, LTy)}
    return res


def tileframe_to_pixframe(zb):
    # 瓦片坐标转化为最终图片的四个角像素的坐标
    out = {}
    width = (zb["RT"][0] - zb["LT"][0] + 1) * 256
    height = (zb["LB"][1] - zb["LT"][1] + 1) * 256
    out["LT"] = (0, 0)
    out["RT"] = (width, 0)
    out["LB"] = (0, -height)
    out["RB"] = (width, -height)
    return out


def mkdir(path):
    path = path.strip()
    path = path.strip('/')
    path = '/'.join(path.replace('\\', '/').split('/')[:-1])
    if not os.path.exists(path):
        os.makedirs(path)


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_7_5) AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/29.0.1547.76 Safari/537.36'}


class Downloader(Thread):
    # multiple threads downloader
    def __init__(self, index, count, urls_filenames, datas):
        # index 表示第几个线程，count 表示线程的总数，urls 代表需要下载url列表，datas代表要返回的数据列表。
        # update 表示每下载一个成功就进行的回调函数。
        super().__init__()
        self.urls_filenames = urls_filenames
        self.datas = datas
        self.index = index
        self.count = count

    @staticmethod
    def download(url, filename):
        if filename and os.path.exists(filename):
            with mutex:
                print("From disk")
            with open(filename, "rb") as f:
                return f.read()
        mkdir(filename)

        with mutex:
            print("downloading", url)
        err = 0
        while err < 3:
            try:
                req = requests.get(url, headers=HEADERS)
                if "html" in req.text[:20]:
                    raise Exception("Server/Network error")
            except Exception as e:
                # traceback.print_exc()
                print(e, file=sys.stderr)
                err += 1
            else:
                if filename:
                    with open(filename, "wb") as f:
                        f.write(req.content)
                with mutex:
                    print("downloaded", url)
                return req.content
        raise Exception("Bad network link.")

    def run(self):
        for i in range(len(self.urls_filenames)):
            url = self.urls_filenames[i][0]
            filename = self.urls_filenames[i][1]
            if i % self.count != self.index:
                continue
            self.datas[i] = self.download(url, filename)


def geturl(source, x, y, z, style):
    """
    Get the picture's url for download.
    style:
        m for map
        s for satellite
    source:
        google or amap or tencent
    x y:
        google-style tile coordinate system
    z:
        zoom
    """
    if source == 'google':
        furl = MAP_URLS["google"].format(x=x, y=y, z=z, style=style)
    elif source == 'amap':
        # for amap 6 is satellite and 7 is map.
        style = 6 if style == 's' else 7
        furl = MAP_URLS["amap"].format(x=x, y=y, z=z, style=style)
    elif source == 'tencent':
        y = 2 ** z - 1 - y
        if style == 's':
            furl = MAP_URLS["tencent_s"].format(
                x=x, y=y, z=z, fx=floor(x / 16), fy=floor(y / 16))
        else:
            furl = MAP_URLS["tencent_m"].format(x=x, y=y, z=z)
    else:
        raise Exception("Unknown Map Source ! ")

    return furl


def downpics(urls_filenames, multi=10):
    url_len = len(urls_filenames)
    datas = [None] * url_len
    if multi < 1 or multi > 20 or not isinstance(multi, int):
        raise Exception("multi of Downloader shuold be int and between 1 to 20.")
    tasks = [Downloader(i, multi, urls_filenames, datas) for i in range(multi)]
    for i in tasks:
        i.start()
    for i in tasks:
        i.join()

    return datas


def num_hash(*args):
    h = 66666
    for e in args:
        h = h * 3666 + e
    return int(h) % 31567


def getpic(x1, y1, x2, y2, z, source='google', outfile="MAP_OUT.png", style='s'):
    """
    依次输入左上角的经度、纬度，右下角的经度、纬度，缩放级别，地图源，输出文件，影像类型（默认为卫星图）
    获取区域内的瓦片并自动拼合图像。返回四个角的瓦片坐标
    """
    pos1x, pos1y = wgs84_to_tile(x1, y1, z)
    pos2x, pos2y = wgs84_to_tile(x2, y2, z)
    lenx = math.ceil(pos2x - pos1x)
    leny = math.ceil(pos2y - pos1y)
    print("Total number：{x} X {y}".format(x=lenx, y=leny))
    urls_filenames = []
    for j in range(int(pos1y), int(pos1y + leny)):
        for i in range(int(pos1x), int(pos1x + lenx)):
            urls_filenames.append((geturl(source, i, j, z, style),
                                   "{source}_{hash}_{z}/{i}_{j}_{name}"
                                   .format(source=source, hash=num_hash(z),
                                           i=i, j=j, z=z, name=outfile)))

    datas = downpics(urls_filenames)

    print("\nDownload Finished！ Pics Merging......")
    outpic = Image.new('RGBA', (int(lenx * 256), int(leny * 256)))
    for i, data in enumerate(datas):
        try:
            small_pic = Image.open(io.BytesIO(data))
            y, x = i // int(lenx), i % int(lenx)
            outpic.paste(small_pic, (x * 256, y * 256))
        except:
            print(i, file=sys.stderr)
            pass

    print('Pics Merged！ Exporting......')
    outpic.save("{source}_{hash}_{z}_{name}"
                .format(source=source, hash=num_hash(x1, y1, x2, y2),
                        z=z, name=outfile))
    print('Exported to file！')
    return {"LT": (pos1x, pos1y), "RT": (pos2x, pos1y), "LB": (pos1x, pos2y), "RB": (pos2x, pos2y), "z": z}


def screen_out(zb, name):
    if not zb:
        print("N/A")
        return
    print("坐标形式：", name)
    print("左上：({0:.5f},{1:.5f})".format(*zb['LT']))
    print("右上：({0:.5f},{1:.5f})".format(*zb['RT']))
    print("左下：({0:.5f},{1:.5f})".format(*zb['LB']))
    print("右下：({0:.5f},{1:.5f})".format(*zb['RB']))


def file_out(zb, file, target="keep", output="file"):
    """
    zh_in  : tile coordinate
    file   : a text file for ArcGis
    target : keep = tile to Geographic coordinate
             gcj  = tile to Geographic coordinate,then wgs84 to gcj
             wgs  = tile to Geographic coordinate,then gcj02 to wgs84
    """
    pixframe = tileframe_to_pixframe(zb)
    Xframe = tileframe_to_mecatorframe(zb)
    for i in ["LT", "LB", "RT", "RB"]:
        Xframe[i] = mecator_to_wgs(*Xframe[i])
    if target == "keep":
        pass;
    elif target == "gcj":
        for i in ["LT", "LB", "RT", "RB"]:
            Xframe[i] = wgs_to_gcj(*Xframe[i])
    elif target == "wgs":
        for i in ["LT", "LB", "RT", "RB"]:
            Xframe[i] = gcj_to_wgs(*Xframe[i])
    else:
        raise Exception("Invalid argument: target.")

    if output == "file":
        f = open(file, "w")
        for i in ["LT", "LB", "RT", "RB"]:
            f.write("{0[0]:.5f}, {0[1].5f}, {1[0].5f}, {1[1].5f}\n".format(pixframe[i], Xframe[i]))
        f.close()
        print("Exported link file to ", file)
    else:
        screen_out(Xframe, target)


if __name__ == '__main__':
    x = getpic(-180, 90, 180, -90,
               6, source='google', style='s', outfile="earth.png")
    # file_out(x, "zb17.txt", "wgs")
