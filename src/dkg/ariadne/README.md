# Ariadne

The refinement community detector, and a full part of the platform.

## Licence

Covered by the repository licence at the root (`LICENSE`): source-available and
free for personal and non-commercial use only. There is no separate licence for
this module, and it is not excluded from the built package.

## What it does

Ariadne refines a base community partition using a refinement-based method with
semantic edge weighting and an auto-tuned resolution. The technique is
modularity optimization; no eponymous algorithm name is used anywhere.

## How it runs

It is the refinement pass in the default community-detection path. A Mnemosyne
base pass runs first, Ariadne refines over the same graph, and whichever
partition scores higher modularity is returned. Selection is by measured
modularity and never by preference, so if the refinement does not improve the
partition the base pass is kept and the result says so.

Run one detector alone with `dkg community --detector mnemosyne` or
`--detector ariadne`. The default is `both`.

Community assignments are structural and advisory, not an authoritative account
of meaning.
