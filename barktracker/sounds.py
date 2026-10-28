"""Plain-language names for the other sounds the YAMNet model recognises, for the live band's
"Hearing a car" note. Only sources are named: dog sounds (those are barks), silence and room-tone
labels like "Inside, small room" never show."""
from __future__ import annotations

# Labels that are the dog itself, or describe the space/background rather than a sound source.
SKIP = {
    "Animal", "Domestic animals, pets", "Dog", "Bark", "Yip", "Howl", "Bow-wow", "Growling",
    "Whimper (dog)", "Canidae, dogs, wolves", "Silence", "Inside, small room", "Inside, large room or hall",
    "Inside, public space", "Outside, urban or manmade", "Outside, rural or natural", "Noise",
    "Environmental noise", "White noise", "Pink noise", "Static", "Hum", "Mains hum", "Sound effect",
    "Echo", "Reverberation", "Field recording", "Background noise",
}

PHRASES = {
    "Vehicle": "a vehicle", "Car": "a car", "Motor vehicle (road)": "a vehicle", "Car passing by": "a car passing",
    "Truck": "a truck", "Bus": "a bus", "Motorcycle": "a motorcycle", "Traffic noise, roadway noise": "traffic",
    "Engine": "an engine", "Idling": "an engine idling", "Accelerating, revving, vroom": "an engine revving",
    "Vehicle horn, car horn, honking": "a car horn", "Car alarm": "a car alarm", "Reversing beeps": "reversing beeps",
    "Skidding": "tires skidding", "Tire squeal": "tires squealing",
    "Emergency vehicle": "an emergency vehicle", "Siren": "a siren", "Police car (siren)": "a police siren",
    "Ambulance (siren)": "an ambulance", "Fire engine, fire truck (siren)": "a fire truck",
    "Aircraft": "an aircraft", "Aircraft engine": "an aircraft", "Jet engine": "a jet", "Airplane": "an airplane",
    "Fixed-wing aircraft, airplane": "an airplane", "Walk, footsteps": "footsteps",
    "Helicopter": "a helicopter", "Train": "a train", "Train horn": "a train horn", "Rail transport": "a train",
    "Bicycle": "a bicycle", "Skateboard": "a skateboard",
    "Lawn mower": "a lawn mower", "Chainsaw": "a chainsaw", "Leaf blower": "a leaf blower",
    "Power tool": "a power tool", "Drill": "a drill", "Hammer": "hammering", "Sawing": "sawing",
    "Jackhammer": "a jackhammer",
    "Speech": "people talking", "Conversation": "people talking", "Male speech, man speaking": "someone talking",
    "Female speech, woman speaking": "someone talking", "Child speech, kid speaking": "a child talking",
    "Shout": "shouting", "Yell": "yelling", "Children shouting": "children shouting", "Screaming": "screaming",
    "Laughter": "laughter", "Baby cry, infant cry": "a baby crying", "Crying, sobbing": "crying",
    "Whistling": "whistling", "Singing": "singing", "Clapping": "clapping", "Footsteps": "footsteps",
    "Music": "music", "Television": "a TV", "Radio": "a radio",
    "Bird": "a bird", "Bird vocalization, bird call, bird song": "birds", "Chirp, tweet": "birds chirping",
    "Crow": "a crow", "Caw": "a crow", "Squawk": "a bird squawking", "Pigeon, dove": "a pigeon",
    "Wild animals": "wildlife", "Livestock, farm animals, working animals": "farm animals",
    "Squirrel": "a squirrel", "Rodents, rats, mice": "a rodent", "Frog": "frogs", "Owl": "an owl",
    "Cat": "a cat", "Meow": "a cat", "Insect": "insects", "Cricket": "crickets",
    "Wind": "wind", "Wind noise (microphone)": "wind on the microphone", "Rain": "rain",
    "Rain on surface": "rain", "Thunder": "thunder", "Thunderstorm": "a thunderstorm", "Water": "water",
    "Door": "a door", "Slam": "a door slam", "Knock": "knocking", "Doorbell": "a doorbell",
    "Ding-dong": "a doorbell", "Telephone": "a phone", "Ringtone": "a phone ringing", "Alarm": "an alarm",
    "Smoke detector, smoke alarm": "a smoke alarm", "Beep, bleep": "beeping", "Fireworks": "fireworks",
    "Gunshot, gunfire": "a bang", "Explosion": "a bang", "Bang": "a bang",
    "Air conditioning": "the air conditioner", "Mechanical fan": "a fan", "Vacuum cleaner": "a vacuum cleaner",
    "Dishes, pots, and pans": "dishes", "Typing": "typing",
}


def phrase(label: str) -> str | None:
    """'Car' -> 'a car'. None for labels that shouldn't be shown."""
    if not label or label in SKIP:
        return None
    if label in PHRASES:
        return PHRASES[label]
    return label.split(",")[0].strip().lower()
