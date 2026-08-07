/* Held-out XS constructs. Written and labelled before it was ever parsed.
 *
 * Everything here is real XS, chosen because a line-shaped extractor has to
 * get each one right for a different reason:
 *
 *   - a pointer return type, where the type line is "SV *" and not a bare word
 *   - a prototype above the MODULE line, which declares and defines nothing
 *   - a definition whose body opens on the following line
 *   - a whole XSUB inside "#if 0", which the file does not define at all
 *   - a whole XSUB inside a block comment, likewise
 *   - an ALIAS section, whose contents are not definitions
 *   - a PACKAGE change that drops a PREFIX, so the prefix must stop applying
 *   - an XSUB whose name happens to begin with the previous section's prefix
 *     while no prefix is in force, which must NOT be stripped
 */

#include "EXTERN.h"
#include "perl.h"
#include "XSUB.h"

/* A prototype. Declares; defines nothing. */
static SV *build_result(int code, const char *message);

/* A definition whose body opens on the next line, returning a pointer. */
static SV *
build_result(int code, const char *message)
{
    SV *out = newSVpv(message, 0);
    sv_setiv(out, code);
    return out;
}

static const char *tag_for(int code) { return code > 0 ? "ok" : "err"; }

MODULE = Hard::Ext    PACKAGE = Hard::Ext    PREFIX = he_

SV *
he_build(code, message)
    int code
    const char *message
  CODE:
    RETVAL = build_result(code, message);
  OUTPUT:
    RETVAL

int
he_status(code)
    int code
  ALIAS:
    Hard::Ext::state = 1
    Hard::Ext::condition = 2
  CODE:
    RETVAL = code;
  OUTPUT:
    RETVAL

#if 0

void
he_disabled()
  CODE:
    never_called();

#endif

/*
void
he_commented_out()
  CODE:
    also_never_called();
*/

MODULE = Hard::Ext    PACKAGE = Hard::Ext::Raw

const char *
he_label(code)
    int code
  CODE:
    RETVAL = tag_for(code);
  OUTPUT:
    RETVAL
