extends CharacterBody2D
class_name Player

const Bullet = preload("res://bullet.gd")

func _ready():
    setup()

func setup():
    reset_state()

func reset_state():
    pass

class Inventory:
    func add_item():
        pass
