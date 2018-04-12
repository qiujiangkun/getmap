package main

import (
	"bytes"
	"fmt"
	"image"
	"image/draw"
	"image/jpeg"
	"io/ioutil"
	"log"
	"math"
	"net/http"
	"os"
)

var (
	urltemplate = map[string]string{"google": "http://mt2.google.cn/vt/lyrs=%s&hl=zh-CN&gl=CN&src=app&x=%d&y=%d&z=%d",
		"amap":      "http://wprd02.is.autonavi.com/appmaptile?style=%d&x=%d&y=%d&z=%d",
		"tencent_s": "http://p3.map.gtimg.com/sateTiles/%d/%d/%d/%d_%d.jpg",
		"tencent_m": "http://rt0.map.gtimg.com/tile?z=%d&x=%d&y=%d&styleid=3"}
	//P2 mean pow(2,i)  i->[0,19]
	P2 = [20]int{1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288}
)

const (
	//AGENT 常用的浏览器user-agent字段
	AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_7_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/29.0.1547.76 Safari/537.36"
)

type urlplace struct {
	url string
	x   int
	y   int
}
type dataplace struct {
	data []byte
	x    int
	y    int
}
type postion struct {
	x, y int
}

// 根据瓦片坐标和地图源，得到瓦片图片的请求地址
func formaturl(source string, x, y, z int, style string) (furl string) {
	/*
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
	*/
	if source == "google" {
		furl = fmt.Sprintf(urltemplate["google"], style, x, y, z)
	} else if source == "amap" {
		// for amap 6 is satellite and 7 is map.
		var s int
		if style == "s" {
			s = 6
		} else {
			s = 7
		}
		furl = fmt.Sprintf(urltemplate["amap"], s, x, y, z)
	} else if source == "tencent" {
		y = P2[z] - 1 - y
		if style == "s" {
			furl = fmt.Sprintf(urltemplate["tencent_s"], z, x/16, y/16, x, y)
		} else {
			furl = fmt.Sprintf(urltemplate["tencent_s"], z, x, y)
		}

	} else {
		log.Fatal("Unknown Map Source!")
	}

	return
}

// 根据WGS-84 的经纬度获取谷歌地图中的瓦片坐标
func wgs84ToTile(j, w float64, z int) (x, y int) {
	/*
	   Get google-style tile cooridinate from geographical coordinate
	   j : Longittude
	   w : Latitude
	   z : zoom
	*/
	if z < 0 || z > 20 {
		log.Fatal("z out of the range [0,19]")
	}
	// make j to (0,1)
	j += 180
	j /= 360

	if w > 85.0511287798 {
		w = 85.0511287798
	}
	if w < -85.0511287798 {
		w = -85.0511287798
	}
	w = math.Log(math.Tan((90+w)*math.Pi/360)) / (math.Pi / 180)
	w /= 180        // make w to (-1,1)
	w = 1 - (w+1)/2 // make w to (0,1) and left top is 0-point

	x = int(j * float64(P2[z]))
	y = int(w * float64(P2[z]))

	return
}

func ccloser(n int, cclose chan int, datac chan dataplace) {
	for i := 0; i < n; i++ {
		<-cclose
	}
	close(datac)

}

// 下载瓦片地图
func downloader(client *http.Client, urlc chan urlplace, datac chan dataplace, cclose chan int, monitorc chan int) {
	for i := range urlc {
		request, _ := http.NewRequest("GET", i.url, nil)
		request.Header.Set("User-Agent", AGENT)
		res, err := client.Do(request)
		if err != nil {
			log.Fatal("download ", i, " Fail !!")
		}
		b, _ := ioutil.ReadAll(res.Body)
		if string(b[1:4]) == "PNG" {
			log.Fatal("对于给定的地图源，此区域没有网络地图信息！\n请尝试减少缩放级别或者更换地图源。")
		}
		monitorc <- 0
		datac <- dataplace{b, i.x, i.y}
	}
	cclose <- 0 //this thread has downloaded
}

// 把瓦片地图合并到一张大图里面
func merger(big *image.NRGBA, datac chan dataplace, outimgc chan *image.NRGBA) {
	var img image.Image
	var err error
	for it := range datac {
		img, err = jpeg.Decode(bytes.NewReader(it.data))
		if err != nil {
			fmt.Println("解析瓦片地图格式错误！")
			log.Fatal(err.Error())
		}
		draw.Draw(big, image.Rect(it.x*256, it.y*256, it.x*256+256, it.y*256+256), img, image.Point{0, 0}, draw.Src)

	}
	outimgc <- big
}
func monitor(sum int, count chan int) {
	for i := 0; i < sum; i++ {
		fmt.Printf("\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b正在下载及拼合图像... [%d/%d]", i+1, sum)
		<-count
	}
	fmt.Println("\n下载完成。还在拼合图像...")
}

// 建立一个大图片
func makeBigImg(width, height int) *image.NRGBA {
	return image.NewNRGBA(image.Rect(0, 0, 256*width, 256*height))
}

//根据矩形对角的两个瓦片坐标，返回出矩形内所有的瓦片坐标,以及瓦片的横纵数量
func makeRange(x0, y0, x1, y1 int) (r []postion, width, height int) {
	/*if x0 > x1 {
		x0, x1 = x1, x0
	}
	if y0 > y1 {
		y0, y1 = y1, y0
	}*/
	height = y1 - y0 + 1
	width = x1 - x0 + 1
	for yt := y0; yt <= y1; yt++ {
		for xt := x0; xt <= x1; xt++ {
			r = append(r, postion{xt, yt})
		}
	}
	return
}

//经度 Longitude
//纬度 Latitude
//Getmap 根据矩形对角的经纬度，下载矩形范围内所有地图，并拼合，写入f中。
func Getmap(f *os.File, source string, maptype string, lng0, lat0, lng1, lat1 float64, z int, multi int) {
	x0, y0 := wgs84ToTile(lng0, lat0, z)
	x1, y1 := wgs84ToTile(lng1, lat1, z)
	tiles, w, h := makeRange(x0, y0, x1, y1)

	big := makeBigImg(w, h)
	client := &http.Client{}

	urlc := make(chan urlplace, 20)
	datac := make(chan dataplace, 20)
	imgc := make(chan *image.NRGBA)
	closec := make(chan int)
	monitorc := make(chan int)

	go monitor(w*h, monitorc)
	go ccloser(multi, closec, datac)
	go merger(big, datac, imgc)

	//多线程下载
	for i := 0; i < multi; i++ {
		go downloader(client, urlc, datac, closec, monitorc)
	}

	for i, v := range tiles {
		urlc <- urlplace{formaturl(source, v.x, v.y, z, maptype), i % w, i / w}
	}
	close(urlc)
	jpeg.Encode(f, <-imgc, nil)

}

func main() {
	f, _ := os.Create("out2.jpg")
	Getmap(f, "google", "s", 108.0, 34.2, 108.1, 34.1, 17, 10)
	f.Close()
	fmt.Println("拼合完成！")

}
