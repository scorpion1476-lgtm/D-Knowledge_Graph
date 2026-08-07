/* Geometry::Shapes, a Perl XS extension.
 *
 * Deliberately written the way real XS is written: a plain C helper above the
 * first MODULE line in the two-line return-type style, then two packages, one
 * of which carries a PREFIX so the Perl-visible name differs from the XSUB
 * name written in the file.
 */

#include "EXTERN.h"
#include "perl.h"
#include "XSUB.h"

#include <math.h>

static double
circle_area(double radius)
{
    return M_PI * radius * radius;
}

static double
rect_area(double width, double height)
{
    return width * height;
}

MODULE = Geometry::Shapes    PACKAGE = Geometry::Shapes    PREFIX = gs_

PROTOTYPES: DISABLE

double
gs_area_of_circle(radius)
    double radius
  CODE:
    RETVAL = circle_area(radius);
  OUTPUT:
    RETVAL

double
gs_area_of_rect(width, height)
    double width
    double height
  CODE:
    RETVAL = rect_area(width, height);
  OUTPUT:
    RETVAL

void
gs_describe()
  PPCODE:
    XPUSHs(sv_2mortal(newSVpv("shapes", 0)));

MODULE = Geometry::Shapes    PACKAGE = Geometry::Shapes::Util

int
clamp(value, low, high)
    int value
    int low
    int high
  CODE:
    RETVAL = value;
    if (value < low) RETVAL = low;
    if (value > high) RETVAL = high;
  OUTPUT:
    RETVAL
