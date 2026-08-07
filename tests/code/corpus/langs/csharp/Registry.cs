using System.Collections.Generic;

namespace Geometry
{
    public class Registry
    {
        private readonly List<object> items = new List<object>();

        public Registry() { }

        public void Add(object item)
        {
            items.Add(item);
        }

        public void Seed()
        {
            Add(Factory.MakeCircle(1.0));
        }
    }
}
