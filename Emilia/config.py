import json
import os


def get_user_list(config, key):
    with open("{}/Emilia/{}".format(os.getcwd(), config), "r") as json_file:
        return json.load(json_file)[key]


class Config(object):
    API_HASH = "45aabfaca930ac474279d9f20dd93a6d"
    API_ID = 6799281

    BOT_ID = 5737513498
    BOT_USERNAME = "7692466190"

    MONGO_DB_URL = "mongodb+srv://elianaapi:pranav8935@cluster0.gf5ky.mongodb.net/myFirstDatabase?retryWrites=true&w=majority"
   
    SUPPORT_CHAT = "SpiralTechDivision"
    UPDATE_CHANNEL = "SpiralUpdates"
    START_PIC = "https://pic-bstarstatic.akamaized.net/ugc/9e98b6c8872450f3e8b19e0d0aca02deff02981f.jpg@1200w_630h_1e_1c_1f.webp"
    DEV_USERS = [6040984893, 6461051572, 7107018652]
    TOKEN = "7692466190:AAHFzu94Fz2n2iGagIZb57gsxfzXriBNh80"
    CLONE_LIMIT = 150

    EVENT_LOGS = -1001591010993
    OWNER_ID = 6040984893

    TEMP_DOWNLOAD_DIRECTORY = "./"
    BOT_NAME = "Emilia"
    WALL_API = "0"


class Production(Config):
    LOGGER = True


class Development(Config):
    LOGGER = True
