#!/usr/bin/env python3
"""Make chains-project maven-lockfile output digestible by hermeto (cachi2) x-maven.

Several defects in the generated lockfiles break hermeto's experimental x-maven
prefetch. Most stem from maven-lockfile recording entries that it could not
attribute to a remote repository (empty/absent ``resolved`` / ``checksum``).

This repo is the ``dev.knative.eventing:connectors`` reactor. Building any one
connector image runs ``mvn package -pl=<connector> -am``, whose reactor is four
modules: ``connectors`` (root pom) -> ``connector-tools`` (``tools``) ->
``connector-maven-plugin`` (``tools/maven-plugin``, built from source and then
run as a build plugin) -> the connector jar. So the hermetic prefetch must cover
the lockfiles of all four (see ``REACTOR_TAIL`` / ``CONNECTORS``), and this
fixup is applied to each of them.

1. Local reactor parent references (``parent`` / ``parentPom`` / nested ``pom``)
   -------------------------------------------------------------------------
   Every module's Maven parent is a *local* reactor module
   (``dev.knative.eventing:connectors`` and, for the plugin,
   ``connector-tools``), recorded WITHOUT a ``resolved`` key. It appears not only
   as the top-level ``pom.parent`` but also as the ``parentPom`` (and nested
   ``parent``) of reactor-local plugin/dependency nodes, at every nesting depth.
   hermeto's ``_extract_pom_chain`` (``models.py``) calls
   ``MavenArtifact.model_validate`` on each such node, and ``resolved`` is a
   required field ->

       ValidationError: 1 validation error for MavenArtifact
       resolved  Field required [type=missing]

   (Note: reactor-local *dependencies* carry ``"resolved": ""`` -- present but
   empty -- so they pass validation; only the parent nodes omit the key and
   crash.) The BOM imports declared by the ``connectors`` parent are remote and
   must be prefetched; they live under the parent's ``boms``, and hermeto
   iterates an artifact's ``boms`` directly. So for each local, unresolved parent
   node we hoist its ``boms`` onto the containing artifact and delete the parent
   reference. The one node we must NOT delete is the module's own top-level
   ``pom`` (hermeto reads it as the root pom); we still strip ITS local
   ``parent``. -> ``strip_local_parents``.

2. External artifacts with empty ``resolved``
   ------------------------------------------
   maven-lockfile sometimes leaves ``resolved``/``checksum`` empty (``""``) for a
   remote artifact it failed to attribute. Its ``parentPom`` may still carry a
   ``resolved`` URL, so the jar exists on that repo; hermeto would otherwise try
   to "download" an empty URL and die with ``FetchError: Could not download``. We
   reconstruct the jar URL from the sibling ``parentPom.resolved`` (repo base +
   repositoryId), fetch the jar once and record its sha256.
   -> ``backfill_external``. (The connector reactor lockfiles currently carry no
   external empties, so this pass is a no-op here; it is kept as a safety net for
   future regenerations and is idempotent.)

3. Reactor-internal ``resolved:""`` nodes -- the ``connector-maven-plugin``
   -----------------------------------------------------------------------
   Each connector uses the reactor-local ``connector-maven-plugin`` as a build
   plugin, so maven-lockfile records it under the top-level ``mavenPlugins`` list
   with ``"resolved": ""`` (present but empty -- built from source, no remote
   jar). Unlike the local *parent* nodes (defect 1, which omit ``resolved`` and
   crash validation), it PASSES ``MavenArtifact.model_validate`` and reaches the
   download phase, where hermeto fetches the empty URL and dies with
   ``FetchError: Could not download``. It cannot be back-filled (no remote
   artifact) and must not simply be deleted: it carries the plugin's real
   external runtime dependencies (``maven-core``, ``camel-k-crds``,
   ``maven-plugin-annotations`` ...) under a ``dependencies`` list, several of
   which appear nowhere else in the lockfile. So we *splice* it: drop the
   reactor-local empty node from its containing list and hoist its (recursively
   spliced) ``children`` AND ``dependencies`` into its place, preserving every
   external descendant. (maven-lockfile 5.18.3 nests the main dependency forest
   under ``children`` but records plugin-level deps under ``dependencies``, so we
   hoist both.) hermeto flattens everything into a de-duplicated set, so the
   resulting duplicates are harmless. -> ``splice_local_deps``.

4. External leaf dependencies whose Maven parent POM is never prefetched
   --------------------------------------------------------------------
   maven-lockfile records the full ``parentPom`` chain for many artifacts, but
   NOT for some transitive leaf dependencies -- even with ``-DincludeParentPom``
   on (the default). Such a node carries only a string ``"parent"`` (its
   *dependency-tree* parent, e.g. ``"parent":
   "io.micrometer:micrometer-core:..."``) and no ``parentPom``, so hermeto never
   learns about the artifact's *Maven* parent POM. hermeto fetches each jar's
   sibling ``.pom``, but does not itself follow the ``<parent>`` declared inside
   it. If that parent POM is not otherwise in the prefetch set (i.e. it is not
   another artifact's jar/pom or another node's recorded parentPom), the offline
   build fails reading the dependency's descriptor::

       Failed to read artifact descriptor for org.latencyutils:LatencyUtils:jar:2.0.3.redhat-00005
       Caused by: ... org.sonatype.oss:oss-parent:pom:7.0.0.redhat-00018 (absent):
       Cannot access hermeto-local (file:///cachi2/output/deps/maven) in offline mode ...

   For every external jar node WITHOUT a ``parentPom``, we read its real ``.pom``,
   walk the ``<parent>`` chain, and -- if any ancestor is missing from this
   lockfile's prefetch set -- record the contiguous chain (immediate parent under
   ``parentPom``, deeper ancestors nested under ``parent``) with fetched sha256s,
   matching the schema maven-lockfile itself uses. hermeto dedups the pom forest,
   so recording it on one occurrence per GAV suffices. -> ``backfill_parent_poms``.

5. Imported BOM POMs (``<dependencyManagement>`` ``<scope>import</scope>``)
   ------------------------------------------------------------------------
   maven-lockfile records the artifacts of the dependency graph and their
   ``<parent>`` chains, but NOT the BOMs those POMs *import*. When Maven reads a
   dependency's descriptor it builds the artifact's effective model, which means
   resolving every ``<dependencyManagement>`` entry with ``<scope>import</scope>``
   declared by the POM or any of its ancestors. hermeto fetches each such POM but
   does not itself follow those import declarations, so an imported BOM that is
   not otherwise in the prefetch set makes the offline build fail reading the
   descriptor::

       Failed to read artifact descriptor for io.smallrye:smallrye-context-propagation:jar:2.3.0
       Caused by: ... org.junit:junit-bom:pom:5.11.0 (absent):
       Cannot access hermeto-local (file:///cachi2/output/deps/maven) in offline mode ...

   (Here the import lives three levels up, in ``smallrye-parent:46``'s
   dependencyManagement, as ``org.junit:junit-bom:${version.junit5}``.) This is
   transitive and property-driven: ``io.vertx:vertx-dependencies:4.5.26`` is
   itself imported, has its own ``<parent>`` (``vertx-parent:22``) and imports
   ``netty-bom``/``jackson-bom`` at property-resolved versions, none of which the
   lockfile carries. So we walk the whole prefetch closure: for every external
   POM already prefetched (plus every BOM we add), we read its own
   ``<dependencyManagement>`` imports, resolve their coordinates against the
   merged properties of that POM's ``<parent>`` chain, and -- for any imported
   BOM missing from the prefetch set -- record it (and its own ``<parent>`` chain)
   as flat ``boms`` entries on the module root pom, then inspect it in turn.
   hermeto iterates an artifact's ``boms`` directly and dedups the pom forest, so
   flat entries suffice. -> ``backfill_bom_imports``.

6. The Quarkus platform descriptor (``*-quarkus-platform-properties``)
   -------------------------------------------------------------------
   ``quarkus-maven-plugin:generate-code`` bootstraps by resolving
   ``io.quarkus.platform:<bom>-quarkus-platform-properties:properties:<ver>`` from
   the platform BOM metadata. It is not a declared dependency (non-standard
   ``properties`` type, no ``.pom``), so maven-lockfile never records it and
   hermeto never prefetches it -> the offline ``generate-code`` fails resolving
   that artifact. We record it as a flat ``boms`` entry pointing at the
   ``.properties`` file. -> ``backfill_platform_properties``.

7. Quarkus extension deployment/augmentation closure
   -------------------------------------------------
   For every *runtime* extension on the classpath the Quarkus bootstrap resolves
   that extension's ``-deployment`` artifact -- discovered from
   ``META-INF/quarkus-extension.properties`` inside the runtime jar, NOT from any
   POM -- plus the deployment artifact's whole transitive tree (further deployment
   artifacts, build-time libraries, classifier jars, and test-scope extension
   deployments). None are declared dependencies, so maven-lockfile never records
   them and the offline ``generate-code``/``build`` dies collecting/resolving
   ``...-deployment:jar:<ver>``. Reproducing Maven+Quarkus resolution in Python
   proved unreliable (version mediation, classifiers, conditional deps, deep
   third-party subtrees), so we use the build itself as the oracle: run the real
   hermetic command online into a throwaway repo and record every jar it resolves
   that the four reactor lockfiles do not already prefetch.
   -> ``backfill_quarkus_deployment_deps``.

Run this after (re)generating the lockfiles with maven-lockfile. Idempotent.
Requires network access to the Red Hat Maven repo and the Maven Central mirror
(a dev-time regen step, NOT the hermetic build).
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LOCAL_GROUP_ID = "dev.knative.eventing"

# The connector image modules; building each runs ``mvn package -pl=<c> -am``.
CONNECTORS = (
    "aws-ddb-streams-source",
    "aws-s3-sink",
    "aws-s3-source",
    "aws-sns-sink",
    "aws-sqs-sink",
    "aws-sqs-source",
    "log-sink",
    "timer-source",
)
# Shared reactor tail pulled in by ``-am`` for every connector (see module
# docstring). Their lockfiles are prefetched alongside the connector's, so they
# are fixed up too. NOTE: ``tools/archetype-{sink,source}`` are deliberately
# excluded -- they are not part of any connector's build reactor and their
# lockfiles carry unrelated ``RELEASE`` metaversion entries.
REACTOR_TAIL = (".", "tools", "tools/maven-plugin")
# Fallback repo for productized (.redhat-*) artifacts when the parentPom URL
# cannot be used to derive the base.
REDHAT_GA_BASE = "https://maven.repository.redhat.com/ga/"
# Maven Central mirror used for non-productized artifacts (matches the
# ``resolved`` URLs and ``repositoryId`` maven-lockfile records for them).
CENTRAL_BASE = "https://maven-central.storage.googleapis.com/maven2/"
POM_NS = "{http://maven.apache.org/POM/4.0.0}"

# Keys under which hermeto's _extract_pom_chain follows a pom reference.
_PARENT_KEYS = ("parent", "parentPom", "pom")


def _hoist_boms(dst: dict, boms: list) -> None:
    """Merge ``boms`` into ``dst['boms']`` (dedup by GAV)."""
    existing = dst.get("boms", [])
    seen = {(b.get("groupId"), b.get("artifactId"), b.get("version")) for b in existing}
    for bom in boms or []:
        key = (bom.get("groupId"), bom.get("artifactId"), bom.get("version"))
        if key not in seen:
            existing.append(bom)
            seen.add(key)
    if existing:
        dst["boms"] = existing


def strip_local_parents(node, protect) -> bool:
    """Drop local reactor parent refs lacking ``resolved``; hoist their BOMs.

    Recurses through the whole document. ``protect`` is the module's own root
    pom dict, which must never be deleted (hermeto reads it as the root pom).
    """
    changed = False
    if isinstance(node, dict):
        for key in _PARENT_KEYS:
            child = node.get(key)
            if (
                isinstance(child, dict)
                and child is not protect
                and child.get("groupId") == LOCAL_GROUP_ID
                and "resolved" not in child
            ):
                _hoist_boms(node, child.get("boms"))
                del node[key]
                changed = True
        for value in node.values():
            changed |= strip_local_parents(value, protect)
    elif isinstance(node, list):
        for value in node:
            changed |= strip_local_parents(value, protect)
    return changed


def _iter_artifacts(node):
    """Yield every dict that looks like a Maven artifact (groupId + resolved)."""
    if isinstance(node, dict):
        if "groupId" in node and "resolved" in node:
            yield node
        for value in node.values():
            yield from _iter_artifacts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_artifacts(value)


def _repo_base(art: dict) -> tuple[str, str]:
    """Derive (base_url, repositoryId) from the artifact's own resolved parentPom."""
    parent = art.get("parentPom")
    if isinstance(parent, dict):
        pr = parent.get("resolved", "")
        gid, aid, ver = parent.get("groupId"), parent.get("artifactId"), parent.get("version")
        if pr and gid and aid and ver:
            suffix = f"{gid.replace('.', '/')}/{aid}/{ver}/{aid}-{ver}.pom"
            if pr.endswith(suffix):
                return pr[: -len(suffix)], (parent.get("repositoryId") or "redhat")
    if ".redhat-" in str(art.get("version", "")):
        return REDHAT_GA_BASE, "redhat"
    raise RuntimeError(
        f"cannot derive repo URL for {art.get('groupId')}:{art.get('artifactId')}:{art.get('version')}"
    )


def _sha256_of(url: str, cache: dict) -> str:
    if url in cache:
        return cache[url]
    req = urllib.request.Request(url, headers={"User-Agent": "hermeto-lockfile-fixup"})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (trusted Maven repo URL)
        digest = hashlib.sha256()
        for chunk in iter(lambda: resp.read(1 << 16), b""):
            digest.update(chunk)
    cache[url] = digest.hexdigest()
    return cache[url]


def backfill_external(data: dict, cache: dict) -> bool:
    """Fill resolved/checksum for external artifacts left empty by maven-lockfile."""
    changed = False
    for art in _iter_artifacts(data):
        if art.get("resolved") != "":
            continue
        if art.get("groupId") == LOCAL_GROUP_ID:
            continue  # reactor-local, built from source; removed by splice_local_deps
        gid, aid, ver = art["groupId"], art["artifactId"], art["version"]
        base, repo_id = _repo_base(art)
        url = f"{base}{gid.replace('.', '/')}/{aid}/{ver}/{aid}-{ver}.jar"
        art["resolved"] = url
        art["repositoryId"] = repo_id
        if not art.get("checksum"):
            art["checksum"] = _sha256_of(url, cache)
            art.setdefault("checksumAlgorithm", "SHA-256")
        changed = True
    return changed


def _splice(value):
    """Return ``(new_value, changed)``.

    Any reactor-local artifact dict with ``"resolved": ""`` that sits inside a
    list (``mavenPlugins`` / ``dependencies`` / ``children``) is removed and
    replaced by its own ``children`` AND ``dependencies`` -- which are themselves
    spliced first, so nested reactor-local nodes at any depth collapse away while
    every external descendant is kept. (maven-lockfile nests the main dependency
    forest under ``children`` but records plugin-level deps under
    ``dependencies``; hoisting both covers the ``connector-maven-plugin`` case.)
    Dicts are recursed in place; lists are rebuilt.
    """
    if isinstance(value, dict):
        changed = False
        for key, child in list(value.items()):
            new_child, ch = _splice(child)
            if ch:
                value[key] = new_child
                changed = True
        return value, changed
    if isinstance(value, list):
        new_list = []
        changed = False
        for item in value:
            new_item, ch = _splice(item)  # splice the item's own children first
            changed |= ch
            if (
                isinstance(new_item, dict)
                and new_item.get("groupId") == LOCAL_GROUP_ID
                and new_item.get("resolved", None) == ""
            ):
                new_list.extend(new_item.get("children", []))
                new_list.extend(new_item.get("dependencies", []))
                changed = True
            else:
                new_list.append(new_item)
        return new_list, changed
    return value, False


def splice_local_deps(data: dict) -> bool:
    """Remove reactor-local ``resolved:""`` deps, hoisting their children."""
    _, changed = _splice(data)
    return changed


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "hermeto-lockfile-fixup"})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (trusted Maven repo URL)
        return resp.read()


def _pom_path(gid: str, aid: str, ver: str) -> str:
    return f"{gid.replace('.', '/')}/{aid}/{ver}/{aid}-{ver}.pom"


def _repo_id_for(url: str) -> str:
    return "redhat" if url.startswith(REDHAT_GA_BASE) else "google-maven-central"


def _fetch_pom(gid: str, aid: str, ver: str, cache: dict, exact_url: str = None):
    """Return ``(bytes, resolved_url)`` for a POM, or ``(None, None)`` if absent.

    Productized ``.redhat`` artifacts usually live on the Red Hat repo and the
    rest on the Central mirror, but parents can cross repos (a Red Hat artifact
    with a Central parent, or vice versa), so we try both. ``exact_url`` (a jar's
    sibling ``.pom``) is preferred when known.
    """
    key = ("pom", gid, aid, ver)
    if key in cache:
        return cache[key]
    ordered = [REDHAT_GA_BASE, CENTRAL_BASE]
    if ".redhat" not in ver:
        ordered.reverse()
    candidates = ([exact_url] if exact_url else []) + [b + _pom_path(gid, aid, ver) for b in ordered]
    for url in dict.fromkeys(candidates):  # de-dup, preserve order
        try:
            cache[key] = (_http_get(url), url)
            return cache[key]
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
    cache[key] = (None, None)
    return cache[key]


def _parent_gav(pom: bytes):
    """Return the ``(groupId, artifactId, version)`` of a POM's ``<parent>``."""
    try:
        root = ET.fromstring(pom)
    except ET.ParseError:
        return None
    parent = root.find(f"{POM_NS}parent")
    if parent is None:
        parent = root.find("parent")  # POMs without the 4.0.0 namespace
    if parent is None:
        return None

    def text(tag):
        el = parent.find(f"{POM_NS}{tag}")
        if el is None:
            el = parent.find(tag)
        return el.text.strip() if (el is not None and el.text) else None

    gid, aid, ver = text("groupId"), text("artifactId"), text("version")
    return (gid, aid, ver) if (gid and aid and ver) else None


def _prefetch_pom_gavs(data: dict) -> set:
    """GAVs of every POM already in this lockfile's prefetch contribution.

    hermeto fetches each jar's sibling ``.pom`` plus every explicitly-recorded
    ``.pom`` (parentPom / parent-dict / boms), so both cover a GAV's descriptor.
    """
    gavs = set()
    for art in _iter_artifacts(data):
        gid, aid, ver = art.get("groupId"), art.get("artifactId"), art.get("version")
        res = art.get("resolved") or ""
        if gid and aid and ver and (res.endswith(".pom") or res.endswith(".jar")):
            gavs.add((gid, aid, ver))
    return gavs


def _missing_parent_chain(node: dict, prefetch: set, cache: dict) -> list:
    """Real ``<parent>`` chain of ``node`` up to its deepest un-prefetched ancestor.

    Returns ``[(gav, url, pom_bytes), ...]`` from immediate parent to the deepest
    missing ancestor (empty if every ancestor is already prefetched). Any
    fully-prefetched tail beyond the last gap is trimmed -- we only add what is
    missing while keeping the chain contiguous from the artifact.
    """
    own = (node["groupId"], node["artifactId"], node["version"])
    pom, _ = _fetch_pom(*own, cache, exact_url=re.sub(r"\.jar$", ".pom", node["resolved"]))
    chain, seen = [], {own}
    while pom is not None:
        parent = _parent_gav(pom)
        if not parent or parent in seen:
            break
        seen.add(parent)
        pom, url = _fetch_pom(*parent, cache)
        if pom is None:  # parent POM in no known repo; cannot resolve further
            break
        chain.append((parent, url, pom))
    last_missing = max(
        (i for i, (gav, _u, _b) in enumerate(chain) if gav not in prefetch), default=-1
    )
    return chain[: last_missing + 1]


def _build_parent_pom(chain: list) -> dict:
    """Nest ``chain`` into a parentPom dict (deepest ancestor under ``parent``)."""
    node = None
    for (gid, aid, ver), url, pom in reversed(chain):
        entry = {
            "groupId": gid,
            "artifactId": aid,
            "version": ver,
            "resolved": url,
            "repositoryId": _repo_id_for(url),
            "checksumAlgorithm": "SHA-256",
            "checksum": hashlib.sha256(pom).hexdigest(),
        }
        if node is not None:
            entry["parent"] = node
        node = entry
    return node


def backfill_parent_poms(data: dict, cache: dict) -> bool:
    """Record Maven parent-POM chains hermeto would otherwise never prefetch."""
    changed = False
    prefetch = _prefetch_pom_gavs(data)
    done = set()
    for art in list(_iter_artifacts(data)):
        res = art.get("resolved") or ""
        if not res.endswith(".jar") or art.get("groupId") == LOCAL_GROUP_ID:
            continue
        if "parentPom" in art or not (art.get("artifactId") and art.get("version")):
            continue
        gav = (art["groupId"], art["artifactId"], art["version"])
        if gav in done:
            continue
        chain = _missing_parent_chain(art, prefetch, cache)
        if not chain:
            continue
        art["parentPom"] = _build_parent_pom(chain)
        done.add(gav)
        prefetch.update(g for g, _u, _b in chain)  # siblings sharing an ancestor now dedup
        changed = True
    return changed


def _localname(tag: str) -> str:
    """Strip any XML namespace, so parsing works with or without the POM ns."""
    return tag.rsplit("}", 1)[-1]


def _pom_properties(pom: bytes) -> dict:
    """Return the ``<properties>`` map of a single POM (namespace-agnostic)."""
    try:
        root = ET.fromstring(pom)
    except ET.ParseError:
        return {}
    out: dict = {}
    for child in root:
        if _localname(child.tag) == "properties":
            for prop in child:
                out[_localname(prop.tag)] = (prop.text or "").strip()
    return out


def _effective_props(gav: tuple, cache: dict) -> dict:
    """Merged ``<properties>`` of ``gav``'s whole ``<parent>`` chain (child wins).

    ``project.*`` is set to ``gav`` itself so imports written as
    ``${project.version}`` resolve to the POM declaring them.
    """
    key = ("props",) + gav
    if key in cache:
        return cache[key]
    chain, cur, seen = [], gav, set()
    while cur and cur not in seen:
        seen.add(cur)
        pom, _ = _fetch_pom(*cur, cache)
        if pom is None:
            break
        chain.append(pom)
        cur = _parent_gav(pom)
    props: dict = {}
    for pom in reversed(chain):  # ancestors first; child properties override
        props.update(_pom_properties(pom))
    props["project.version"] = props["version"] = gav[2]
    props["project.groupId"] = gav[0]
    props["project.artifactId"] = gav[1]
    cache[key] = props
    return props


def _resolve_prop(value, props: dict, depth: int = 0):
    """Substitute ``${...}`` placeholders using ``props`` (bounded recursion)."""
    if not value or "${" not in value or depth > 10:
        return value
    new = re.sub(r"\$\{([^}]+)\}", lambda m: props.get(m.group(1), m.group(0)), value)
    return _resolve_prop(new, props, depth + 1) if new != value else new


def _bom_imports(pom: bytes) -> list:
    """Raw ``(groupId, artifactId, version)`` of this POM's own import-scope BOMs."""
    try:
        root = ET.fromstring(pom)
    except ET.ParseError:
        return []
    out = []
    for dm in root.iter():
        if _localname(dm.tag) != "dependencyManagement":
            continue
        for dep in dm.iter():
            if _localname(dep.tag) != "dependency":
                continue
            fields = {_localname(c.tag): (c.text or "").strip() for c in dep}
            if fields.get("scope") == "import":
                gid, aid, ver = fields.get("groupId"), fields.get("artifactId"), fields.get("version")
                if gid and aid and ver:
                    out.append((gid, aid, ver))
    return out


def _pom_url_of(art: dict) -> str:
    """The ``.pom`` URL for an artifact node whose ``resolved`` is a jar or pom."""
    res = art.get("resolved") or ""
    return re.sub(r"\.jar$", ".pom", res) if res.endswith(".jar") else res


def _bom_entry(gav: tuple, url: str, pom: bytes) -> dict:
    gid, aid, ver = gav
    return {
        "groupId": gid,
        "artifactId": aid,
        "version": ver,
        "resolved": url,
        "repositoryId": _repo_id_for(url),
        "checksumAlgorithm": "SHA-256",
        "checksum": hashlib.sha256(pom).hexdigest(),
    }


def backfill_bom_imports(data: dict, root_pom: dict, cache: dict) -> bool:
    """Record BOMs imported by any prefetched POM (transitively) so hermeto fetches them.

    Walks the prefetch closure breadth-first: for each POM it reads the artifact's
    own ``<dependencyManagement>`` imports (versions resolved against the merged
    ``<parent>``-chain properties) and its ``<parent>``; any coordinate missing
    from the prefetch set is fetched, recorded as a flat ``boms`` entry on
    ``root_pom``, and inspected in turn. External (non-reactor) POMs only.
    """
    if root_pom is None:
        return False
    prefetch = _prefetch_pom_gavs(data)
    new_boms: list = []
    queue: deque = deque()
    seed: dict = {}  # gav -> best-known .pom url (may be None)
    for art in _iter_artifacts(data):
        gid, aid, ver = art.get("groupId"), art.get("artifactId"), art.get("version")
        res = art.get("resolved") or ""
        if not (gid and aid and ver) or gid == LOCAL_GROUP_ID:
            continue
        if res.endswith(".pom") or res.endswith(".jar"):
            gav = (gid, aid, ver)
            seed.setdefault(gav, _pom_url_of(art))
    # Warm the pom cache concurrently: the closure is ~1k network fetches per
    # lockfile and reading them serially dominates the runtime.
    with ThreadPoolExecutor(max_workers=24) as pool:
        list(pool.map(lambda it: _fetch_pom(*it[0], cache, exact_url=it[1]), seed.items()))
    queue.extend(seed.items())
    inspected: set = set()

    def ensure(gav: tuple, exact_url: str = None) -> None:
        """Fetch ``gav``'s pom; if not prefetched, record a boms entry. Enqueue it."""
        if gav in prefetch:
            if gav not in inspected:
                queue.append((gav, exact_url))
            return
        pom, url = _fetch_pom(*gav, cache, exact_url=exact_url)
        if pom is None:
            print(f"  warn: cannot resolve BOM/parent {gav[0]}:{gav[1]}:{gav[2]}", file=sys.stderr)
            return
        new_boms.append(_bom_entry(gav, url, pom))
        prefetch.add(gav)
        queue.append((gav, url))

    while queue:
        gav, exact_url = queue.popleft()
        if gav in inspected:
            continue
        inspected.add(gav)
        pom, url = _fetch_pom(*gav, cache, exact_url=exact_url)
        if pom is None:
            continue
        parent = _parent_gav(pom)
        if parent and parent[0] != LOCAL_GROUP_ID:
            ensure(parent)
        imports = _bom_imports(pom)
        if not imports:
            continue
        props = _effective_props(gav, cache)
        for gid, aid, ver in imports:
            r_gid = _resolve_prop(gid, props)
            r_aid = _resolve_prop(aid, props)
            r_ver = _resolve_prop(ver, props)
            if not r_ver or "${" in r_ver or r_gid == LOCAL_GROUP_ID:
                continue
            ensure((r_gid, r_aid, r_ver))

    if new_boms:
        _hoist_boms(root_pom, new_boms)
    return bool(new_boms)


QUARKUS_PLATFORM_GID = "io.quarkus.platform"
QUARKUS_PLATFORM_BOM_AID = "quarkus-bom"

# The hermetic connector build command (see the module docstring). Run online at
# regen time into a throwaway repo, it resolves the EXACT closure the offline
# build needs -- including the Quarkus deployment tree -- so we can diff it
# against the prefetch set. Env var ``QDEP_PREBUILT_REPOS`` may point at a dir
# holding ``pkgrepo-<module>`` repos to reuse instead of rebuilding (dev speed).
_BUILD_GOALS = ("package", "-am", "-Drelease", "-DskipTests", "-DskipArchetypes")


def backfill_platform_properties(data: dict, root_pom: dict, cache: dict) -> bool:
    """Record the Quarkus platform descriptor (``*-quarkus-platform-properties``).

    The ``quarkus-maven-plugin:generate-code`` bootstrap resolves
    ``io.quarkus.platform:<platform-bom>-quarkus-platform-properties:properties:<ver>``
    from the platform BOM's metadata. It is NOT a declared dependency (and has a
    non-standard ``properties`` type with no ``.pom``), so maven-lockfile never
    records it -- even when declared explicitly as a ``<type>properties</type>``
    dependency it is dropped -- and hermeto never prefetches it, so the offline
    build dies at ``generate-code``:

        Failed to resolve artifact io.quarkus.platform:
        quarkus-bom-quarkus-platform-properties:properties:<ver> (absent):
        Cannot access hermeto-local ... in offline mode

    We record it as a flat ``boms`` entry -- hermeto fetches each ``boms``
    ``resolved`` URL verbatim and mirrors it into the offline repo by its Maven
    path -- pointing at the ``.properties`` file. Version is taken from the
    platform BOM actually present in the lockfile. Idempotent.

    Only modules that actually run ``quarkus-maven-plugin`` (the connectors) need
    the descriptor; reactor-tail modules (root/tools) merely *import* the BOM in
    dependencyManagement and never bootstrap, so they are skipped.
    """
    if root_pom is None:
        return False
    if not any(
        a.get("artifactId") == "quarkus-maven-plugin" for a in _iter_artifacts(data)
    ):
        return False
    ver = next(
        (
            a.get("version")
            for a in _iter_artifacts(data)
            if a.get("groupId") == QUARKUS_PLATFORM_GID
            and a.get("artifactId") == QUARKUS_PLATFORM_BOM_AID
            and a.get("version")
        ),
        None,
    )
    if not ver:
        return False
    aid = f"{QUARKUS_PLATFORM_BOM_AID}-quarkus-platform-properties"
    gav = (QUARKUS_PLATFORM_GID, aid, ver)
    if gav in {
        (b.get("groupId"), b.get("artifactId"), b.get("version"))
        for b in root_pom.get("boms", [])
    }:
        return False
    url = (
        f"{CENTRAL_BASE}{QUARKUS_PLATFORM_GID.replace('.', '/')}/{aid}/{ver}"
        f"/{aid}-{ver}.properties"
    )
    _hoist_boms(
        root_pom,
        [
            {
                "groupId": QUARKUS_PLATFORM_GID,
                "artifactId": aid,
                "version": ver,
                "resolved": url,
                "repositoryId": _repo_id_for(url),
                "checksumAlgorithm": "SHA-256",
                "checksum": _sha256_of(url, cache),
            }
        ],
    )
    return True


def _artifact_node(gid: str, aid: str, ver: str, jar_url: str, jar_sha: str, classifier=None) -> dict:
    """A minimal maven-lockfile artifact node (the schema hermeto's x-maven reads)."""
    node = {
        "groupId": gid,
        "artifactId": aid,
        "version": ver,
        "checksumAlgorithm": "SHA-256",
        "checksum": jar_sha,
        "scope": "compile",
        "resolved": jar_url,
        "repositoryId": _repo_id_for(jar_url),
        "selectedVersion": ver,
        "included": True,
        "id": f"{gid}:{aid}:{ver}",
    }
    if classifier:
        node["classifier"] = classifier
    return node


def _coords_from_repo_jar(jar: Path, repo: Path) -> tuple:
    """(groupId, artifactId, version, classifier) from a jar's path in a Maven repo.

    Layout is ``<group/path>/<aid>/<ver>/<aid>-<ver>[-<classifier>].jar``, so the
    directory structure gives gid/aid/ver unambiguously (versions may contain
    hyphens); the classifier is whatever remains of the filename.
    """
    parts = jar.relative_to(repo).parts
    ver, aid = parts[-2], parts[-3]
    gid = ".".join(parts[:-3])
    stem = jar.name[:-4]  # strip ".jar"
    prefix = f"{aid}-{ver}"
    classifier = stem[len(prefix) + 1:] if (stem != prefix and stem.startswith(prefix + "-")) else None
    return gid, aid, ver, classifier


def _reactor_union_jar_keys(root: Path, data: dict) -> set:
    """Every jar ``(gid, aid, ver, classifier)`` hermeto already prefetches.

    A connector build prefetches the union of all four reactor lockfiles (root,
    ``tools``, ``tools/maven-plugin`` and the connector itself), so an artifact
    present in *any* of them needs no re-recording here.
    """
    keys = set()

    def collect(doc):
        for a in _iter_artifacts(doc):
            gid, aid, ver = a.get("groupId"), a.get("artifactId"), a.get("version")
            if gid and aid and ver and (a.get("resolved") or "").endswith(".jar"):
                keys.add((gid, aid, ver, a.get("classifier")))

    collect(data)
    for rel in ("lockfile.json", "tools/lockfile.json", "tools/maven-plugin/lockfile.json"):
        sibling = root / rel
        if sibling.exists():
            collect(json.loads(sibling.read_text()))
    return keys


def _build_full_closure_repo(root: Path, module: str) -> Path:
    """Run the hermetic build command online into a throwaway local repo.

    The resulting repo holds the exact artifact closure the offline build
    resolves for ``module`` (runtime + Quarkus deployment/augmentation tree).
    """
    repo = Path(tempfile.mkdtemp(prefix=f"qdep-{module}-"))
    cmd = ["./mvnw", "-q", *(_BUILD_GOALS[:1]), f"-pl={module}", *_BUILD_GOALS[1:],
           f"-Dmaven.repo.local={repo}", "--no-transfer-progress"]
    subprocess.run(cmd, cwd=str(root), check=True)
    return repo


def backfill_quarkus_deployment_deps(path: Path, data: dict, cache: dict) -> bool:
    """Prefetch the Quarkus extension deployment/augmentation closure.

    ``quarkus-maven-plugin`` (``generate-code`` + ``build``) runs the Quarkus
    bootstrap resolver, which for every *runtime* extension on the classpath
    resolves that extension's ``-deployment`` artifact -- discovered from
    ``META-INF/quarkus-extension.properties`` inside the runtime jar, NOT from any
    POM -- plus the deployment artifact's whole transitive tree (other deployment
    artifacts, build-time libraries, classifier jars such as
    ``io.dekorate:*-annotations:noapt``, and test-scope extension deployments like
    ``citrus-quarkus-deployment``). None of these are declared project
    dependencies, so maven-lockfile never records them (regenerating it would not
    either) and hermeto never prefetches them; the offline build then dies at
    ``generate-code`` with ``Failed to collect/resolve ...-deployment`` /
    ``Cannot access hermeto-local ... in offline mode``.

    Reproducing that resolution in Python is unreliable (version mediation,
    classifiers, conditional dependencies, deep third-party subtrees), so we let
    Maven/Quarkus be the oracle: run the real hermetic build command
    (``mvn package -pl=<module> -am -Drelease -DskipTests -DskipArchetypes``)
    online into a throwaway local repo, which contains the exact closure the
    offline build needs. Every jar in that repo not already prefetched by the four
    reactor lockfiles is recorded as an artifact node (classifier included).
    Parent-POM and imported-BOM edges of the added jars are completed by
    ``backfill_parent_poms`` / ``backfill_bom_imports`` (which run after this
    pass). Only connector modules run ``quarkus-maven-plugin``; reactor-tail
    modules never bootstrap and are skipped. Set ``QDEP_PREBUILT_REPOS`` to a dir
    of ``pkgrepo-<module>`` repos to reuse instead of rebuilding.
    -> ``backfill_quarkus_deployment_deps``.
    """
    if not any(a.get("artifactId") == "quarkus-maven-plugin" for a in _iter_artifacts(data)):
        return False

    module = data.get("artifactId")
    root = path.resolve().parent.parent  # <root>/<module>/lockfile.json
    prebuilt = os.environ.get("QDEP_PREBUILT_REPOS")
    reuse = Path(prebuilt) / f"pkgrepo-{module}" if prebuilt else None
    if reuse and reuse.is_dir():
        repo, owned = reuse, False
    else:
        repo, owned = _build_full_closure_repo(root, module), True

    try:
        union = _reactor_union_jar_keys(root, data)
        deps_list = data.setdefault("dependencies", [])
        changed = False
        for jar in sorted(repo.rglob("*.jar")):
            if jar.name.endswith(("-sources.jar", "-javadoc.jar")):
                continue
            gid, aid, ver, classifier = _coords_from_repo_jar(jar, repo)
            if gid == LOCAL_GROUP_ID:
                continue  # reactor modules, built from source
            key = (gid, aid, ver, classifier)
            if key in union:
                continue
            suffix = f"-{classifier}" if classifier else ""
            url = f"{CENTRAL_BASE}{gid.replace('.', '/')}/{aid}/{ver}/{aid}-{ver}{suffix}.jar"
            deps_list.append(
                _artifact_node(gid, aid, ver, url, hashlib.sha256(jar.read_bytes()).hexdigest(), classifier)
            )
            union.add(key)
            changed = True
        return changed
    finally:
        if owned:
            shutil.rmtree(repo, ignore_errors=True)


def process(path: Path, cache: dict) -> bool:
    data = json.loads(path.read_text())
    root_pom = data.get("pom") if isinstance(data.get("pom"), dict) else None
    changed = strip_local_parents(data, root_pom)
    changed |= backfill_external(data, cache)
    changed |= splice_local_deps(data)
    changed |= backfill_quarkus_deployment_deps(path, data, cache)
    changed |= backfill_parent_poms(data, cache)
    changed |= backfill_bom_imports(data, root_pom, cache)
    changed |= backfill_platform_properties(data, root_pom, cache)
    if changed:
        path.write_text(json.dumps(data, indent=2) + "\n")
    return changed


def _reactor_lockfiles(root: Path) -> list[Path]:
    """The lockfiles prefetched for the connector image builds (see docstring)."""
    paths = [root / m / "lockfile.json" for m in (*REACTOR_TAIL, *CONNECTORS)]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise SystemExit("missing lockfile(s): " + ", ".join(str(p) for p in missing))
    return paths


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    cache: dict = {}
    changed = 0
    for lf in _reactor_lockfiles(root):
        if process(lf, cache):
            print(f"fixed: {lf}")
            changed += 1
        else:
            print(f"skip:  {lf}")
    print(f"\n{changed} lockfile(s) fixed for hermeto")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
