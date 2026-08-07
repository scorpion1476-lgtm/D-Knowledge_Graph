using System;
using System.Collections.Generic;

namespace Geometry
{
    public interface IDrawable
    {
        void Draw();
    }

    public class Shape : IDrawable
    {
        public void Draw() { }

        public virtual double Area()
        {
            return 0.0;
        }
    }

    public class Circle : Shape
    {
        private readonly double radius;

        public Circle(double radius)
        {
            this.radius = radius;
        }

        public override double Area()
        {
            return Math.PI * radius * radius;
        }
    }

    public struct Point
    {
        public double X;
    }

    public enum Kind
    {
        Round,
        Square
    }

    public class Factory
    {
        public static Circle MakeCircle(double radius)
        {
            return new Circle(radius);
        }

        public static double TotalArea(List<Shape> shapes)
        {
            double total = 0.0;
            foreach (var shape in shapes)
            {
                total += shape.Area();
            }
            return total;
        }
    }
}
