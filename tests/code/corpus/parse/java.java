class Animal {
    void eat() {
    }
}

class Dog extends Animal {
    void bark() {
        this.eat();
    }
}

class Helpers {
    static Dog makeDog() {
        return new Dog();
    }

    static int javaHelper() {
        return 5;
    }
}
