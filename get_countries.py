import json


def get_countries():
    try:
        with open("seed_profiles.json", "r") as f:
            seed_data = json.load(f)

        profiles = seed_data.get("profiles", [])
        countries = list({(d["country_id"], d["country_name"]) for d in profiles})
        countries = [{"country_id": cid, "country_name": name} for cid, name in countries]
        countries.sort(key=lambda c: c["country_id"])

        with open("countries.json", "w") as out:
            json.dump(countries, out, indent=4)
    except Exception as e:
        print(f"Error loading countries: {e}")
        raise


if __name__ == "__main__":
    get_countries()
