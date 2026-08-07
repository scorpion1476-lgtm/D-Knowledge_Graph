/* Geometry::Registry, a second XS file with no PREFIX in force.
 *
 * Without a PREFIX the Perl-visible name is the XSUB name exactly as written,
 * which is the case the prefix-stripping rule must not damage. It also carries
 * a BOOT section and a single-line C helper, both of which appear in real XS.
 */

#include "EXTERN.h"
#include "perl.h"
#include "XSUB.h"

static int registry_count = 0;

static int bump_count(int by) { return registry_count += by; }

MODULE = Geometry::Registry    PACKAGE = Geometry::Registry

BOOT:
    registry_count = 0;

int
add(count)
    int count
  CODE:
    RETVAL = bump_count(count);
  OUTPUT:
    RETVAL

int
total()
  CODE:
    RETVAL = registry_count;
  OUTPUT:
    RETVAL

void
reset()
  CODE:
    registry_count = 0;
