package geometry

import scala.collection.mutable.ListBuffer

class Registry {
  private val items = ListBuffer[Any]()
  def add(item: Any): Unit = items += item
  def seed(): Unit = add(Factory.makeCircle(1.0))
}

object RegistryFactory {
  def empty(): Registry = new Registry()
}
