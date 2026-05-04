from app.database import engine, create_db_and_tables
from sqlmodel import Session, select
from app.model.profiles import Profile
import json


def seed():
    try:
        with open("seed_profiles.json", "r") as f:
            print("Loading seed data... 🔄")
            seed_data = json.load(f)
            print("Seed data loaded successfully ✅")
    except Exception as e:
        print(f"Error loading seed data: {e}")
        raise
    try:
        profiles = seed_data.get("profiles", [])
        with Session(engine) as session:
            print("Seeding data... 🔄")
            for i, profile in enumerate(profiles):
                name = profile["name"]
                statement = select(Profile).where(Profile.name == name)
                existing_profile = session.exec(statement).first()
                if existing_profile:
                    continue
                db_profile = Profile.model_validate(profile)
                session.add(db_profile)
                print(f"Profile {i + 1} seeded successfully 🎉")
            session.commit()
            print("Data seeded successfully 🎉")
    except Exception as e:
        print(f"Error seeding data: {e}")
        raise


if __name__ == "__main__":
    create_db_and_tables()
    seed()
