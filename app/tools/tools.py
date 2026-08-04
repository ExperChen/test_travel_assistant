from importlib import import_module
from importlib.util import find_spec
import json
import re
from datetime import date, datetime, time, timedelta

from dotenv import load_dotenv
from langchain.tools import tool

from app.tools.currency import EXCHANGE_TO_MYR as _PLANNER_FIXED_MYR_RATES


TRAVEL_ATTRACTION_CATALOG = {
    "bangkok": [
        {
            "name": "The Grand Palace",
            "location": "Phra Nakhon, Bangkok",
            "information": "泰国皇室地标，建筑华丽",
            "price": 500.00,
            "currency": "THB",
            "open_time": "08:30-15:30",
            "suggested_duration_hours": 3,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/c/c4/Grand_Palace_Bangkok.jpg",
        },
        {
            "name": "Wat Pho",
            "location": "Phra Nakhon, Bangkok",
            "information": "卧佛闻名，寺院历史悠久",
            "price": 300.00,
            "currency": "THB",
            "open_time": "08:00-18:30",
            "suggested_duration_hours": 2,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/1/1e/Wat_Pho_Bangkok.jpg",
        },
        {
            "name": "Wat Arun",
            "location": "Bangkok Yai, Bangkok",
            "information": "郑王庙临河，夕景迷人",
            "price": 200.00,
            "currency": "THB",
            "open_time": "08:00-18:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "16:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/a/a1/Wat_Arun_Bangkok.jpg",
        },
        {
            "name": "Jim Thompson House Museum",
            "location": "Pathum Wan, Bangkok",
            "information": "泰丝名宅，艺术氛围浓厚",
            "price": 200.00,
            "currency": "THB",
            "open_time": "10:00-18:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "10:30",
            "image": "https://upload.wikimedia.org/wikipedia/commons/9/95/Jim_Thompson_House.jpg",
        },
        {
            "name": "Chatuchak Weekend Market",
            "location": "Chatuchak, Bangkok",
            "information": "大型市集，购物美食丰富",
            "price": 0.00,
            "currency": "THB",
            "open_time": "09:00-18:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "14:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/d/d5/Chatuchak_Market_Bangkok.jpg",
        },
    ],
    "pattaya": [
        {
            "name": "Sanctuary of Truth",
            "location": "Na Kluea, Pattaya",
            "information": "全木雕神殿，工艺震撼",
            "price": 500.00,
            "currency": "THB",
            "open_time": "08:00-18:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "09:30",
            "image": "https://upload.wikimedia.org/wikipedia/commons/8/82/Sanctuary_of_Truth_Pattaya.jpg",
        },
        {
            "name": "Nong Nooch Tropical Garden",
            "location": "Sattahip, Pattaya",
            "information": "热带园林秀，亲子热门",
            "price": 600.00,
            "currency": "THB",
            "open_time": "08:00-18:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/0/06/Nong_Nooc_Tropical_Garden.jpg",
        },
        {
            "name": "Pattaya Floating Market",
            "location": "Bang Lamung, Pattaya",
            "information": "水上市集，体验泰式风情",
            "price": 200.00,
            "currency": "THB",
            "open_time": "09:00-19:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "10:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/2/22/Pattaya_Floating_Market.jpg",
        },
        {
            "name": "Big Buddha Temple",
            "location": "South Pattaya, Pattaya",
            "information": "山顶大佛，俯瞰芭堤雅湾",
            "price": 100.00,
            "currency": "THB",
            "open_time": "07:00-19:00",
            "suggested_duration_hours": 1,
            "preferred_start_time": "16:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/7/71/Wat_Phra_Yai_Pattaya.jpg",
        },
        {
            "name": "Art in Paradise Pattaya",
            "location": "North Pattaya, Pattaya",
            "information": "互动3D美术馆，拍照有趣",
            "price": 400.00,
            "currency": "THB",
            "open_time": "09:00-21:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "13:30",
            "image": "https://upload.wikimedia.org/wikipedia/commons/9/92/Art_in_Paradise_Pattaya.jpg",
        },
    ],
    "tokyo": [
        {
            "name": "Tokyo Tower",
            "location": "Minato, Tokyo",
            "information": "东京经典观景塔，适合俯瞰城市天际线。",
            "price": 1500.00,
            "currency": "JPY",
            "open_time": "09:00-22:30",
            "suggested_duration_hours": 2,
            "preferred_start_time": "17:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/3/37/Tokyo_Tower_and_around_Skyscrapers.jpg",
        },
        {
            "name": "Sensō-ji",
            "location": "Asakusa, Tokyo",
            "information": "浅草地标寺院，适合第一次到东京的游客。",
            "price": 0.00,
            "currency": "JPY",
            "open_time": "06:00-17:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/a/a5/Sensoji_2023.jpg",
        },
        {
            "name": "Meiji Shrine",
            "location": "Shibuya, Tokyo",
            "information": "位于森林步道中的神社，氛围安静。",
            "price": 0.00,
            "currency": "JPY",
            "open_time": "06:00-18:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "10:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/8/89/Meiji_Shrine_Honden_2023.jpg",
        },
        {
            "name": "Shibuya Scramble Crossing",
            "location": "Shibuya, Tokyo",
            "information": "东京最具代表性的都市街景之一，白天夜晚都适合打卡。",
            "price": 0.00,
            "currency": "JPY",
            "open_time": "00:00-23:59",
            "suggested_duration_hours": 1,
            "preferred_start_time": "19:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/5/5d/Shibuya_Night_%282018%29.jpg",
        },
    ],
    "singapore": [
        {
            "name": "Gardens by the Bay",
            "location": "Marina Bay, Singapore",
            "information": "滨海湾超级树与温室花园，是新加坡最热门地标之一。",
            "price": 0.00,
            "currency": "SGD",
            "open_time": "05:00-02:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "18:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/a/a7/Gardens_by_the_Bay_Supertree_Grove_2019.jpg",
        },
        {
            "name": "Marina Bay Sands SkyPark",
            "location": "Marina Bay, Singapore",
            "information": "高空观景平台，适合看滨海湾夜景。",
            "price": 35.00,
            "currency": "SGD",
            "open_time": "11:00-21:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "19:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/e/e6/Marina_Bay_Sands_in_the_evening_-_20101120.jpg",
        },
        {
            "name": "Merlion Park",
            "location": "Downtown Core, Singapore",
            "information": "新加坡鱼尾狮地标，适合与滨海湾一并游览。",
            "price": 0.00,
            "currency": "SGD",
            "open_time": "00:00-23:59",
            "suggested_duration_hours": 1,
            "preferred_start_time": "08:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/0/0f/Merlion_Park%2C_Singapore_-_20110224.jpg",
        },
        {
            "name": "Singapore Botanic Gardens",
            "location": "Tanglin, Singapore",
            "information": "世界遗产植物园，适合轻松散步。",
            "price": 0.00,
            "currency": "SGD",
            "open_time": "05:00-00:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/1/13/Singapore_Botanic_Gardens_ECO_lake.jpg",
        },
    ],
    "seoul": [
        {
            "name": "Gyeongbokgung Palace",
            "location": "Jongno-gu, Seoul",
            "information": "朝鲜王朝代表性宫殿，适合首次到首尔的游客。",
            "price": 3000.00,
            "currency": "KRW",
            "open_time": "09:00-18:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/e/e7/Gyeongbokgung-Geunjeongjeon.jpg",
        },
        {
            "name": "Bukchon Hanok Village",
            "location": "Jongno-gu, Seoul",
            "information": "传统韩屋街区，适合散步拍照并体验首尔旧城风貌。",
            "price": 0.00,
            "currency": "KRW",
            "open_time": "10:00-17:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/5/59/Korea-Seoul-Bukchon-Hanok-Maeul-01.jpg",
        },
        {
            "name": "N Seoul Tower",
            "location": "Yongsan-gu, Seoul",
            "information": "首尔经典观景地标，适合傍晚登塔看城市夜景。",
            "price": 21000.00,
            "currency": "KRW",
            "open_time": "10:00-23:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "18:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/3/3d/N_Seoul_Tower_at_night.JPG",
        },
        {
            "name": "Changdeokgung Palace",
            "location": "Jongno-gu, Seoul",
            "information": "世界遗产宫殿，后苑景观尤其受欢迎。",
            "price": 3000.00,
            "currency": "KRW",
            "open_time": "09:00-17:30",
            "suggested_duration_hours": 2,
            "preferred_start_time": "10:30",
            "image": "https://upload.wikimedia.org/wikipedia/commons/8/8b/Changdeokgung-Injeongjeon.jpg",
        },
    ],
    "beijing": [
        {
            "name": "The Palace Museum",
            "location": "Dongcheng, Beijing",
            "information": "故宫博物院是北京最具代表性的历史地标之一。",
            "price": 60.00,
            "currency": "CNY",
            "open_time": "08:30-17:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/a/a1/Forbidden_City_Beijing_Shenwumen_Gate.JPG",
        },
        {
            "name": "Temple of Heaven",
            "location": "Dongcheng, Beijing",
            "information": "天坛是北京经典皇家祭天建筑群，园区宽阔。",
            "price": 34.00,
            "currency": "CNY",
            "open_time": "08:00-17:30",
            "suggested_duration_hours": 2,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/a/a4/Temple_of_Heaven_Beijing_2.JPG",
        },
        {
            "name": "Summer Palace",
            "location": "Haidian, Beijing",
            "information": "颐和园以昆明湖和长廊闻名，适合慢游。",
            "price": 30.00,
            "currency": "CNY",
            "open_time": "09:00-17:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "10:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/8/82/Summer_Palace_Long_Corridor.JPG",
        },
        {
            "name": "Mutianyu Great Wall",
            "location": "Huairou, Beijing",
            "information": "慕田峪长城风景开阔，适合第一次登长城。",
            "price": 45.00,
            "currency": "CNY",
            "open_time": "08:30-17:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "10:30",
            "image": "https://upload.wikimedia.org/wikipedia/commons/1/10/201905_Mutianyu_Great_Wall.jpg",
        },
    ],
    "shanghai": [
        {
            "name": "The Bund",
            "location": "Huangpu, Shanghai",
            "information": "外滩是上海最经典的滨江天际线观景区。",
            "price": 0.00,
            "currency": "CNY",
            "open_time": "00:00-23:59",
            "suggested_duration_hours": 2,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/a/a4/Bund_Shanghai.jpg",
        },
        {
            "name": "Yu Garden",
            "location": "Huangpu, Shanghai",
            "information": "豫园是上海老城厢代表园林景点。",
            "price": 40.00,
            "currency": "CNY",
            "open_time": "09:00-16:30",
            "suggested_duration_hours": 2,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/6/6a/Yu_Garden_Shanghai_2018.jpg",
        },
        {
            "name": "Oriental Pearl Tower",
            "location": "Pudong, Shanghai",
            "information": "东方明珠是上海最知名的城市地标之一。",
            "price": 199.00,
            "currency": "CNY",
            "open_time": "09:00-21:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "16:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/5/58/Oriental_Pearl_Tower_2019.jpg",
        },
        {
            "name": "Shanghai Museum",
            "location": "Huangpu, Shanghai",
            "information": "上博馆藏丰富，适合安排半天文化行程。",
            "price": 0.00,
            "currency": "CNY",
            "open_time": "09:00-17:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "10:30",
            "image": "https://upload.wikimedia.org/wikipedia/commons/6/69/Shanghai_Museum_2015.jpg",
        },
    ],
    "kuala lumpur": [
        {
            "name": "Petronas Twin Towers",
            "location": "Kuala Lumpur City Centre",
            "information": "吉隆坡双子塔是城市天际线核心地标。",
            "price": 98.00,
            "currency": "MYR",
            "open_time": "09:00-21:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/9/9f/Petronas_Twin_Towers_%28cropped%29.jpg",
        },
        {
            "name": "KL Tower",
            "location": "Bukit Nanas, Kuala Lumpur",
            "information": "吉隆坡塔适合俯瞰市区与拍摄夜景。",
            "price": 49.00,
            "currency": "MYR",
            "open_time": "09:00-22:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/0/09/Kuala_Lumpur_Tower_2014.jpg",
        },
        {
            "name": "Batu Caves",
            "location": "Gombak, Selangor",
            "information": "黑风洞是吉隆坡周边最热门的宗教与自然景点之一。",
            "price": 0.00,
            "currency": "MYR",
            "open_time": "07:00-21:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "10:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/8/8f/Batu_Caves_2022.jpg",
        },
        {
            "name": "Central Market",
            "location": "Kuala Lumpur",
            "information": "中央艺术坊适合买手信和体验本地文化。",
            "price": 0.00,
            "currency": "MYR",
            "open_time": "10:00-22:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "16:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/e/e0/Central_Market_Kuala_Lumpur.jpg",
        },
    ],
    "penang": [
        {
            "name": "Penang Hill",
            "location": "Air Itam, Penang",
            "information": "升旗山可俯瞰槟城景观，适合半日行程。",
            "price": 30.00,
            "currency": "MYR",
            "open_time": "06:15-23:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/0/05/Penang_Hill_Funicular.jpg",
        },
        {
            "name": "Kek Lok Si Temple",
            "location": "Air Itam, Penang",
            "information": "极乐寺是槟城最著名的佛教寺庙群。",
            "price": 0.00,
            "currency": "MYR",
            "open_time": "08:30-17:30",
            "suggested_duration_hours": 2,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/4/49/Kek_Lok_Si_Temple_2017.jpg",
        },
        {
            "name": "Chew Jetty",
            "location": "George Town, Penang",
            "information": "姓周桥是槟城乔治市极具代表性的海上聚落。",
            "price": 0.00,
            "currency": "MYR",
            "open_time": "00:00-23:59",
            "suggested_duration_hours": 1,
            "preferred_start_time": "10:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/7/71/Chew_Jetty.jpg",
        },
        {
            "name": "Pinang Peranakan Mansion",
            "location": "George Town, Penang",
            "information": "娘惹博物馆适合了解槟城土生华人文化。",
            "price": 25.00,
            "currency": "MYR",
            "open_time": "09:30-17:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "15:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/8/84/Pinang_Peranakan_Mansion.jpg",
        },
    ],
    "paris": [
        {
            "name": "Eiffel Tower",
            "location": "Paris",
            "information": "巴黎最经典的地标，适合登塔看城市景观。",
            "price": 36.00,
            "currency": "EUR",
            "open_time": "09:30-23:45",
            "suggested_duration_hours": 2,
            "preferred_start_time": "10:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/a/a8/Tour_Eiffel_Wikimedia_Commons.jpg",
        },
        {
            "name": "Louvre Museum",
            "location": "Paris",
            "information": "卢浮宫藏品丰富，适合安排半天以上文化行程。",
            "price": 22.00,
            "currency": "EUR",
            "open_time": "09:00-18:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/a/af/Louvre_Museum_Wikimedia_Commons.jpg",
        },
    ],
    "london": [
        {
            "name": "Tower of London",
            "location": "London",
            "information": "伦敦历史地标，适合看王室与城堡文化。",
            "price": 34.80,
            "currency": "GBP",
            "open_time": "09:00-17:30",
            "suggested_duration_hours": 3,
            "preferred_start_time": "09:30",
            "image": "https://upload.wikimedia.org/wikipedia/commons/6/67/Tower_of_London_viewed_from_the_River_Thames.jpg",
        },
        {
            "name": "British Museum",
            "location": "London",
            "information": "大英博物馆适合雨天与文化行程。",
            "price": 0.00,
            "currency": "GBP",
            "open_time": "10:00-17:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/a/a7/British_Museum_from_NE_2.JPG",
        },
    ],
    "rome": [
        {
            "name": "Colosseum",
            "location": "Rome",
            "information": "罗马斗兽场是古罗马遗迹代表。",
            "price": 18.00,
            "currency": "EUR",
            "open_time": "09:00-19:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "09:30",
            "image": "https://upload.wikimedia.org/wikipedia/commons/d/de/Colosseo_2020.jpg",
        },
        {
            "name": "Roman Forum",
            "location": "Rome",
            "information": "适合与斗兽场连走的古罗马遗址区。",
            "price": 18.00,
            "currency": "EUR",
            "open_time": "09:00-19:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/8/8f/Roman_Forum_viewed_from_Capitoline_Hill.jpg",
        },
    ],
    "barcelona": [
        {
            "name": "Sagrada Família",
            "location": "Barcelona",
            "information": "巴塞罗那最知名建筑地标。",
            "price": 26.00,
            "currency": "EUR",
            "open_time": "09:00-20:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "10:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/2/20/Sagrada_Familia_01.jpg",
        },
        {
            "name": "Park Güell",
            "location": "Barcelona",
            "information": "高迪风格公园，适合拍照和看城市景观。",
            "price": 10.00,
            "currency": "EUR",
            "open_time": "09:30-19:30",
            "suggested_duration_hours": 2,
            "preferred_start_time": "16:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/a/a6/Park_Guell_Barcelona.jpg",
        },
    ],
    "amsterdam": [
        {
            "name": "Rijksmuseum",
            "location": "Amsterdam",
            "information": "荷兰国立博物馆适合艺术爱好者。",
            "price": 25.00,
            "currency": "EUR",
            "open_time": "09:00-17:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "10:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/0/0c/Rijksmuseum_Amsterdam.jpg",
        },
        {
            "name": "Anne Frank House",
            "location": "Amsterdam",
            "information": "安妮之家是阿姆斯特丹最热门历史景点之一。",
            "price": 16.00,
            "currency": "EUR",
            "open_time": "09:00-22:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "14:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/7/75/Anne_Frank_House_Amsterdam.jpg",
        },
    ],
    "dubai": [
        {
            "name": "Burj Khalifa",
            "location": "Dubai",
            "information": "迪拜哈利法塔适合高空看城市全景。",
            "price": 179.00,
            "currency": "AED",
            "open_time": "08:00-23:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "10:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/9/93/Burj_Khalifa.jpg",
        },
        {
            "name": "Dubai Mall Aquarium",
            "location": "Dubai",
            "information": "大型室内水族馆，适合亲子行程。",
            "price": 169.00,
            "currency": "AED",
            "open_time": "10:00-23:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "14:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/2/2b/Dubai_Aquarium.jpg",
        },
    ],
    "sydney": [
        {
            "name": "Sydney Opera House",
            "location": "Sydney",
            "information": "悉尼歌剧院是澳洲最经典地标之一。",
            "price": 43.00,
            "currency": "AUD",
            "open_time": "09:00-17:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "10:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/4/40/Sydney_Opera_House_Sails.jpg",
        },
        {
            "name": "Taronga Zoo Sydney",
            "location": "Sydney",
            "information": "适合看澳洲动物与海港风景。",
            "price": 51.00,
            "currency": "AUD",
            "open_time": "09:30-16:30",
            "suggested_duration_hours": 3,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/9/97/Taronga_Zoo_Wharf_and_Sydney_Harbour.jpg",
        },
    ],
    "melbourne": [
        {
            "name": "Royal Botanic Gardens Victoria",
            "location": "Melbourne",
            "information": "墨尔本经典城市绿地，适合轻松散步。",
            "price": 0.00,
            "currency": "AUD",
            "open_time": "07:30-17:30",
            "suggested_duration_hours": 2,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/5/5d/Royal_Botanic_Gardens_Melbourne.jpg",
        },
        {
            "name": "Queen Victoria Market",
            "location": "Melbourne",
            "information": "适合买本地特产和体验市场氛围。",
            "price": 0.00,
            "currency": "AUD",
            "open_time": "09:00-17:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/1/1c/Queen_Victoria_Market.jpg",
        },
    ],
    "new york": [
        {
            "name": "Statue of Liberty",
            "location": "New York",
            "information": "纽约最经典的自由女神像行程。",
            "price": 25.50,
            "currency": "USD",
            "open_time": "09:00-17:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/a/a1/Statue_of_Liberty_7.jpg",
        },
        {
            "name": "Metropolitan Museum of Art",
            "location": "New York",
            "information": "大都会艺术博物馆适合安排半天文化行程。",
            "price": 30.00,
            "currency": "USD",
            "open_time": "10:00-17:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/7/7e/Metropolitan_Museum_of_Art_%28The_Met%29_Logo.svg",
        },
    ],
    "los angeles": [
        {
            "name": "Universal Studios Hollywood",
            "location": "Los Angeles",
            "information": "洛杉矶热门主题乐园。",
            "price": 109.00,
            "currency": "USD",
            "open_time": "09:00-19:00",
            "suggested_duration_hours": 4,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/e/e5/Universal_Studios_Hollywood.jpg",
        },
        {
            "name": "Griffith Observatory",
            "location": "Los Angeles",
            "information": "适合看洛杉矶城市和好莱坞标志景观。",
            "price": 0.00,
            "currency": "USD",
            "open_time": "12:00-22:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "18:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/2/2f/Griffith_Observatory%2C_June_2022.jpg",
        },
    ],
    "san francisco": [
        {
            "name": "Golden Gate Bridge",
            "location": "San Francisco",
            "information": "旧金山代表性地标，适合步行或骑行观景。",
            "price": 0.00,
            "currency": "USD",
            "open_time": "00:00-23:59",
            "suggested_duration_hours": 2,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/0/0c/GoldenGateBridge-001.jpg",
        },
        {
            "name": "Alcatraz Island",
            "location": "San Francisco",
            "information": "恶魔岛适合历史和海湾风景行程。",
            "price": 45.25,
            "currency": "USD",
            "open_time": "09:00-18:30",
            "suggested_duration_hours": 3,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/0/0d/Alcatraz_Island_photo_D_Ramey_Logan.jpg",
        },
    ],
    "las vegas": [
        {
            "name": "Bellagio Fountains",
            "location": "Las Vegas",
            "information": "拉斯维加斯大道经典夜景打卡点。",
            "price": 0.00,
            "currency": "USD",
            "open_time": "15:00-23:30",
            "suggested_duration_hours": 1,
            "preferred_start_time": "20:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/8/8a/Bellagio_fountains.jpg",
        },
        {
            "name": "High Roller",
            "location": "Las Vegas",
            "information": "高空摩天轮适合看夜景。",
            "price": 35.00,
            "currency": "USD",
            "open_time": "12:00-00:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "18:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/9/9d/High_Roller_Las_Vegas.jpg",
        },
    ],
    "hong kong": [
        {
            "name": "Victoria Peak",
            "location": "Hong Kong",
            "information": "太平山顶适合俯瞰香港天际线。",
            "price": 88.00,
            "currency": "HKD",
            "open_time": "07:00-22:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "17:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/0/0e/Hong_Kong_from_Victoria_Peak.jpg",
        },
        {
            "name": "Hong Kong Disneyland",
            "location": "Hong Kong",
            "information": "适合亲子与主题乐园行程。",
            "price": 639.00,
            "currency": "HKD",
            "open_time": "10:30-20:30",
            "suggested_duration_hours": 4,
            "preferred_start_time": "10:30",
            "image": "https://upload.wikimedia.org/wikipedia/commons/9/9d/Hong_Kong_Disneyland.jpg",
        },
    ],
    "taipei": [
        {
            "name": "Taipei 101",
            "location": "Taipei",
            "information": "台北101观景台适合看城市全景。",
            "price": 600.00,
            "currency": "TWD",
            "open_time": "11:00-21:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "17:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/0/00/Taipei_101_from_afar.jpg",
        },
        {
            "name": "National Palace Museum",
            "location": "Taipei",
            "information": "台北故宫博物院适合安排半天文化行程。",
            "price": 350.00,
            "currency": "TWD",
            "open_time": "09:00-17:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "10:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/6/6d/National_Palace_Museum.jpg",
        },
    ],
    "osaka": [
        {
            "name": "Osaka Castle",
            "location": "Osaka",
            "information": "大阪城是大阪代表性历史地标。",
            "price": 600.00,
            "currency": "JPY",
            "open_time": "09:00-17:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "09:30",
            "image": "https://upload.wikimedia.org/wikipedia/commons/3/3f/Osaka_Castle_02bs3200.jpg",
        },
        {
            "name": "Universal Studios Japan",
            "location": "Osaka",
            "information": "大阪热门主题乐园。",
            "price": 8600.00,
            "currency": "JPY",
            "open_time": "09:00-21:00",
            "suggested_duration_hours": 4,
            "preferred_start_time": "10:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/1/17/Universal_Studios_Japan_Globe.jpg",
        },
    ],
    "kyoto": [
        {
            "name": "Kiyomizu-dera",
            "location": "Kyoto",
            "information": "清水寺是京都最经典寺院之一。",
            "price": 400.00,
            "currency": "JPY",
            "open_time": "06:00-18:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/3/35/Kiyomizu-dera_in_Kyoto-r.jpg",
        },
        {
            "name": "Fushimi Inari Taisha",
            "location": "Kyoto",
            "information": "千本鸟居是京都超人气打卡点。",
            "price": 0.00,
            "currency": "JPY",
            "open_time": "00:00-23:59",
            "suggested_duration_hours": 2,
            "preferred_start_time": "16:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/0/0e/Fushimi_Inari_Taisha%2C_Kyoto%2C_November_2016_-02.jpg",
        },
    ],
    "busan": [
        {
            "name": "Gamcheon Culture Village",
            "location": "Busan",
            "information": "釜山彩色山城聚落，适合步行拍照。",
            "price": 0.00,
            "currency": "KRW",
            "open_time": "09:00-18:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "10:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/9/9e/Gamcheon_Culture_Village.jpg",
        },
        {
            "name": "Haedong Yonggungsa",
            "location": "Busan",
            "information": "海边寺庙景观独特，是釜山代表景点之一。",
            "price": 0.00,
            "currency": "KRW",
            "open_time": "04:00-19:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "14:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/0/05/Haedong_Yonggungsa_Temple.jpg",
        },
    ],
    "jeju": [
        {
            "name": "Seongsan Ilchulbong",
            "location": "Jeju",
            "information": "城山日出峰是济州最知名自然地标。",
            "price": 5000.00,
            "currency": "KRW",
            "open_time": "07:30-19:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/f/f0/Seongsan_Ilchulbong_tuff_cone.jpg",
        },
        {
            "name": "Manjanggul Lava Tube",
            "location": "Jeju",
            "information": "万丈窟是济州经典火山地貌景点。",
            "price": 4000.00,
            "currency": "KRW",
            "open_time": "09:00-18:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/1/10/Manjanggul_cave.jpg",
        },
    ],
    "lisbon": [
        {
            "name": "Belém Tower",
            "location": "Lisbon",
            "information": "里斯本经典海边历史地标。",
            "price": 8.00,
            "currency": "EUR",
            "open_time": "10:00-17:30",
            "suggested_duration_hours": 2,
            "preferred_start_time": "10:30",
            "image": "https://upload.wikimedia.org/wikipedia/commons/7/7c/Belem_Tower_Lisbon.jpg",
        },
        {
            "name": "Jerónimos Monastery",
            "location": "Lisbon",
            "information": "热罗尼莫斯修道院是葡萄牙代表建筑之一。",
            "price": 12.00,
            "currency": "EUR",
            "open_time": "10:00-17:30",
            "suggested_duration_hours": 2,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/3/3f/Mosteiro_dos_Jeronimos_Lisboa.jpg",
        },
    ],
    "vienna": [
        {
            "name": "Schönbrunn Palace",
            "location": "Vienna",
            "information": "维也纳最热门宫殿景点之一。",
            "price": 27.00,
            "currency": "EUR",
            "open_time": "08:30-17:30",
            "suggested_duration_hours": 3,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/9/96/Schloss_Schoenbrunn_Wien_2014.jpg",
        },
        {
            "name": "St. Stephen's Cathedral",
            "location": "Vienna",
            "information": "维也纳市中心经典地标教堂。",
            "price": 0.00,
            "currency": "EUR",
            "open_time": "06:00-22:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "14:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/5/5f/Wien_-_Stephansdom.JPG",
        },
    ],
    "chicago": [
        {
            "name": "Willis Tower Skydeck",
            "location": "Chicago",
            "information": "芝加哥高空观景台，适合看城市天际线。",
            "price": 32.00,
            "currency": "USD",
            "open_time": "09:00-22:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "17:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/9/96/Willis_Tower_from_the_lake.jpg",
        },
        {
            "name": "Art Institute of Chicago",
            "location": "Chicago",
            "information": "芝加哥艺术博物馆适合文化行程。",
            "price": 32.00,
            "currency": "USD",
            "open_time": "11:00-17:00",
            "suggested_duration_hours": 3,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/a/a7/Art_Institute_of_Chicago.jpg",
        },
    ],
    "vancouver": [
        {
            "name": "Stanley Park",
            "location": "Vancouver",
            "information": "温哥华经典海边公园，适合步行骑行。",
            "price": 0.00,
            "currency": "CAD",
            "open_time": "00:00-23:59",
            "suggested_duration_hours": 2,
            "preferred_start_time": "09:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/2/25/Stanley_Park_Seawall.jpg",
        },
        {
            "name": "Capilano Suspension Bridge Park",
            "location": "Vancouver",
            "information": "温哥华热门自然体验景点。",
            "price": 79.95,
            "currency": "CAD",
            "open_time": "09:00-18:00",
            "suggested_duration_hours": 2,
            "preferred_start_time": "13:00",
            "image": "https://upload.wikimedia.org/wikipedia/commons/a/a4/Capilano_Suspension_Bridge.jpg",
        },
    ],
}


FALLBACK_ATTRACTION = {
    "name": "City Landmark Tour",
    "location": "Central District",
    "information": "经典城市地标，轻松游览",
    "price": 300.00,
    "currency": "MYR",
    "open_time": "09:00-17:00",
    "suggested_duration_hours": 2,
    "preferred_start_time": "10:00",
    "image": "https://upload.wikimedia.org/wikipedia/commons/a/ac/No_image_available.svg",
}

_PLANNER_DEFAULT_START_DATE = date(2026, 1, 1)
_CITY_KEY_ALIASES = {
    "北京": "beijing",
    "beijing": "beijing",
    "上海": "shanghai",
    "shanghai": "shanghai",
    "吉隆坡": "kuala lumpur",
    "kuala lumpur": "kuala lumpur",
    "kuala lumpur malaysia": "kuala lumpur",
    "槟城": "penang",
    "檳城": "penang",
    "penang": "penang",
    "penang malaysia": "penang",
    "首尔": "seoul",
    "首爾": "seoul",
    "seoul": "seoul",
    "东京": "tokyo",
    "tokyo": "tokyo",
    "曼谷": "bangkok",
    "bangkok": "bangkok",
    "芭堤雅": "pattaya",
    "pattaya": "pattaya",
    "新加坡": "singapore",
    "singapore": "singapore",
}


def _load_geopy_modules():
    geopy_spec = find_spec("geopy")
    if geopy_spec is None:
        return None, None

    try:
        geocoders_module = import_module("geopy.geocoders")
        distance_module = import_module("geopy.distance")
    except Exception:
        return None, None

    return getattr(geocoders_module, "Nominatim", None), getattr(distance_module, "geodesic", None)


def _parse_json_query(query: str) -> dict:
    if isinstance(query, dict):
        return query

    text = str(query or "").strip()
    if not text:
        return {}

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}

    return payload if isinstance(payload, dict) else {}


def _safe_parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _normalize_city_key(city: str) -> str:
    value = str(city or "").strip().lower()
    compact = re.sub(r"\s+", " ", value.replace(",", " ")).strip()
    return _CITY_KEY_ALIASES.get(compact, compact)


def _trip_dates(start_date: date, end_date: date) -> list[date]:
    day_count = (end_date - start_date).days + 1
    return [start_date + timedelta(days=offset) for offset in range(max(day_count, 0))]


def _parse_hour_minute(value: str) -> tuple[int, int]:
    hour_text, minute_text = value.split(":", 1)
    return int(hour_text), int(minute_text)


def _combine_datetime(day: date, hour_text: str) -> datetime:
    hour, minute = _parse_hour_minute(hour_text)
    return datetime.combine(day, time(hour=hour, minute=minute))


def _format_duration(hours: int) -> str:
    return f"{hours} hour" if hours == 1 else f"{hours} hours"


def _normalize_planner_price(price: float) -> float:
    try:
        return float(f"{float(price):.2f}")
    except (TypeError, ValueError):
        return 0.0


def _planner_currency_code(value: str | None = None, fallback: str | None = None) -> str:
    text = str(value or "").strip().upper()
    token_map = {
        "MYR": "MYR",
        "RM": "MYR",
        "THB": "THB",
        "CNY": "CNY",
        "RMB": "CNY",
        "JPY": "JPY",
        "SGD": "SGD",
        "KRW": "KRW",
        "₩": "KRW",
        "USD": "USD",
        "$": "USD",
    }
    for token, code in token_map.items():
        if token in text:
            return code
    fallback_text = str(fallback or "").strip().upper()
    return token_map.get(fallback_text, fallback_text or "MYR")


def _convert_price_to_myr(amount: float, currency: str | None = None) -> float:
    normalized_currency = _planner_currency_code(fallback=currency)
    rate = _PLANNER_FIXED_MYR_RATES.get(normalized_currency, 1.0)
    return _normalize_planner_price(float(amount) * rate)


def _parse_numeric_ticket_price(value: str, currency: str | None = None) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    if text.lower() == "free":
        return 0.0

    match = re.search(r"(\d+(?:\.\d+)?)", text.replace(",", ""))
    if not match:
        return 0.0
    amount = float(match.group(1))
    return _convert_price_to_myr(amount, _planner_currency_code(text, fallback=currency))


def _planner_text_has_noise(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return True
    noise_patterns = [
        r"&#x[0-9a-f]+;",
        r"cookie",
        r"跳過導航|跳到課文|快捷鍵",
        r"官方旅遊資訊網站|official tourism website|visit seoul",
        r"close\s+-->|-->\s*-->",
        r"menu",
        r"browse.*record|browsing.*record",
    ]
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in noise_patterns)


def _planner_name_is_usable(name: str, city: str) -> bool:
    value = str(name or "").strip()
    if not value:
        return False
    lowered = value.lower()
    city_text = str(city or "").strip().lower()
    if len(value) > 80:
        return False
    if _planner_text_has_noise(value):
        return False
    if "|" in value or "｜" in value:
        return False
    if re.search(r"景點|景点|熱門景點|热门景点|official tourism website|旅遊資訊網站|旅游资讯网站", value, re.IGNORECASE):
        return False
    if re.search(r"top\s*\d+|best\s+\d+|自由行|行程|攻略", value, re.IGNORECASE):
        return False
    if city_text and lowered == city_text:
        return False
    return True


def _planner_information_text(item: dict, city: str, name: str) -> str:
    raw = str(item.get("brief_description") or item.get("description") or "").strip()
    if not raw or _planner_text_has_noise(raw):
        return f"{name} 是 {city} 的热门景点。".strip()
    return raw[:240].strip()


def _planner_default_time_window(index: int) -> tuple[str, str, int]:
    slots = [
        ("09:00", "09:00-18:00", 3),
        ("13:00", "09:00-18:00", 2),
        ("16:00", "09:00-20:00", 2),
        ("10:30", "09:00-18:00", 2),
    ]
    return slots[index % len(slots)]


def _load_attraction_recommendation_getter():
    try:
        module = import_module("app.tools.attraction_tool")
    except Exception:
        return None
    getter = getattr(module, "get_attractions_by_place", None)
    return getter if callable(getter) else None


def _planner_attractions_from_recommendations(city: str) -> list[dict]:
    load_dotenv()
    getter = _load_attraction_recommendation_getter()
    if getter is None:
        return []

    try:
        recommendations = getter(place=city, query_type=f"{city} attractions")
    except Exception:
        return []
    if not isinstance(recommendations, list):
        return []

    normalized: list[dict] = []
    seen_names: set[str] = set()
    for index, item in enumerate(recommendations):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not _planner_name_is_usable(name, city):
            continue
        lowered_name = name.lower()
        if lowered_name in seen_names:
            continue
        seen_names.add(lowered_name)

        preferred_start_time, open_time, duration_hours = _planner_default_time_window(index)
        normalized.append(
            {
                "name": name,
                "location": str(item.get("location") or city).strip() or city,
                "information": _planner_information_text(item, city, name),
                "price": _parse_numeric_ticket_price(item.get("ticket_price"), str(item.get("currency") or "")),
                "currency": "MYR",
                "open_time": open_time,
                "suggested_duration_hours": duration_hours,
                "preferred_start_time": preferred_start_time,
                "image": str(item.get("image") or "").strip(),
            }
        )
    return normalized


def _normalize_trip_payload(query: str | dict) -> tuple[list[str], date, date, int]:
    payload = _parse_json_query(query)
    cities = [str(city).strip() for city in payload.get("cities", []) if str(city).strip()]
    if not cities:
        cities = ["Trip City"]

    parsed_start_date = _safe_parse_date(payload.get("start_date"))
    parsed_end_date = _safe_parse_date(payload.get("end_date"))
    start_date = parsed_start_date or _PLANNER_DEFAULT_START_DATE
    end_date = parsed_end_date or start_date
    if parsed_start_date is None or parsed_end_date is None:
        end_date = start_date
    if end_date < start_date:
        end_date = start_date

    travelers_raw = payload.get("travelers", 1)
    try:
        travelers = int(travelers_raw)
    except (TypeError, ValueError):
        travelers = 1
    travelers = max(1, travelers)

    return cities, start_date, end_date, travelers


def _build_view(day: date, attraction: dict) -> dict:
    open_start, open_end = attraction["open_time"].split("-", 1)
    arrival_time = _combine_datetime(day, attraction["preferred_start_time"])
    open_start_time = _combine_datetime(day, open_start)
    open_end_time = _combine_datetime(day, open_end)

    if arrival_time < open_start_time:
        arrival_time = open_start_time

    duration_hours = int(attraction["suggested_duration_hours"])
    departure_time = arrival_time + timedelta(hours=duration_hours)
    if departure_time > open_end_time:
        departure_time = open_end_time
        adjusted_hours = max(1, int((departure_time - arrival_time).total_seconds() // 3600))
        duration_hours = adjusted_hours
        arrival_time = departure_time - timedelta(hours=duration_hours)
        if arrival_time < open_start_time:
            arrival_time = open_start_time
            departure_time = min(arrival_time + timedelta(hours=duration_hours), open_end_time)

    duration_hours = max(1, int((departure_time - arrival_time).total_seconds() // 3600) or duration_hours)
    return {
        "name": attraction["name"],
        "location": attraction["location"],
        "information": attraction["information"],
        "price": _convert_price_to_myr(attraction["price"], attraction.get("currency")),
        "open_time": attraction["open_time"],
        "arrival_time": arrival_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "departure_time": departure_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "visit_duration": _format_duration(duration_hours),
        "image": attraction["image"],
    }


def _attractions_for_city(city: str) -> list[dict]:
    catalog = TRAVEL_ATTRACTION_CATALOG.get(_normalize_city_key(city), [])
    if catalog:
        return [dict(item) for item in catalog]

    if city and city != "Trip City":
        recommended = _planner_attractions_from_recommendations(city)
        if recommended:
            return recommended

    fallback = dict(FALLBACK_ATTRACTION)
    fallback["name"] = f"{city} City Landmark Tour" if city else fallback["name"]
    fallback["location"] = f"Central District, {city}" if city else fallback["location"]
    return [fallback]


def _build_structured_travel_plan(query: str) -> dict:
    cities, start_date, end_date, _travelers = _normalize_trip_payload(query)
    trip_days = _trip_dates(start_date, end_date)
    if not trip_days:
        return {"views": []}

    city_sequences: list[str] = []
    if len(cities) >= len(trip_days):
        city_sequences = cities[: len(trip_days)]
    else:
        base_days = len(trip_days) // len(cities)
        extra_days = len(trip_days) % len(cities)
        for index, city in enumerate(cities):
            assigned_days = base_days + (1 if index < extra_days else 0)
            city_sequences.extend([city] * assigned_days)

    views: list[dict] = []
    city_offsets: dict[str, int] = {}
    for day, city in zip(trip_days, city_sequences):
        attractions = _attractions_for_city(city)
        planned_count = min(2, len(attractions))
        start_index = city_offsets.get(city, 0)
        selected_attractions: list[dict] = []
        for offset in range(planned_count):
            attraction_index = (start_index + offset) % len(attractions)
            selected_attractions.append(attractions[attraction_index])
        city_offsets[city] = start_index + planned_count
        selected_attractions.sort(key=lambda item: item["preferred_start_time"])
        for attraction in selected_attractions:
            views.append(_build_view(day, attraction))

    return {"views": views}


@tool
def get_location_info(place: str) -> str:
    """
    地图 API 工具：获取地点的详细地址与经纬度坐标
    - 输入：地名（如 "Tokyo Tower" 或 "东京塔"）
    - 输出：详细地址、纬度、经度
    """
    nominatim_cls, _ = _load_geopy_modules()
    if nominatim_cls is None:
        return "地图查询出错：缺少 geopy 依赖，请执行 `pip install -r requirements.txt`。"

    try:
        # 使用 Nominatim（OpenStreetMap）不需要 Key，但需要设置 user_agent
        geolocator = nominatim_cls(user_agent="ai_travel_agent")
        location = geolocator.geocode(place)

        if location:
            return f"地点：{place}\n地址：{location.address}\n坐标：({location.latitude}, {location.longitude})"
        else:
            return f"未找到地点：{place}，请尝试更具体的名称。"
    except Exception as e:
        return f"地图查询出错：{str(e)}"


@tool
def calculate_distance(place_a: str, place_b: str) -> str:
    """
    地图 API 工具：计算两个地点之间的直线距离（公里）
    - 输入：起始地、目的地
    - 输出：距离（km）
    """
    nominatim_cls, geodesic_func = _load_geopy_modules()
    if nominatim_cls is None or geodesic_func is None:
        return "距离计算出错：缺少 geopy 依赖，请执行 `pip install -r requirements.txt`。"

    try:
        geolocator = nominatim_cls(user_agent="ai_travel_agent")
        loc_a = geolocator.geocode(place_a)
        loc_b = geolocator.geocode(place_b)

        if loc_a and loc_b:
            coords_a = (loc_a.latitude, loc_a.longitude)
            coords_b = (loc_b.latitude, loc_b.longitude)
            distance = geodesic_func(coords_a, coords_b).kilometers
            return f"{place_a} 与 {place_b} 的直线距离约为：{distance:.2f} 公里"
        else:
            return "无法找到其中一个地点的坐标，请检查地名。"
    except Exception as e:
        return f"距离计算出错：{str(e)}"


@tool
def travel_planner(query: str) -> str:
    """
    旅行规划工具：根据 JSON 输入生成严格 JSON 行程规划
    - 输入：包含 cities/start_date/end_date/travelers 的 JSON 字符串
    - 输出：{"views":[...]} JSON 字符串
    """
    return json.dumps(_build_structured_travel_plan(query), ensure_ascii=False)
