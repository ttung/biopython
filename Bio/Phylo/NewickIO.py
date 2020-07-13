# Copyright (C) 2009 by Eric Talevich (eric.talevich@gmail.com)
# Based on Bio.Nexus, copyright 2005-2008 by Frank Kauff & Cymon J. Cox.
# All rights reserved.
#
# This file is part of the Biopython distribution and governed by your
# choice of the "Biopython License Agreement" or the "BSD 3-Clause License".
# Please see the LICENSE file that should have been included as part of this
# package.

"""I/O function wrappers for the Newick file format.

See: http://evolution.genetics.washington.edu/phylip/newick_doc.html
"""

import re
from collections import deque
from io import StringIO

from Bio.Phylo import Newick


class NewickError(Exception):
    """Exception raised when Newick object construction cannot continue."""

    pass


tokens = [
    (r"\(", "open parens"),
    (r"\)", "close parens"),
    (r"[^\s\(\)\[\]\'\:\;\,]+", "unquoted node label"),
    (r"\:\ ?[+-]?[0-9]*\.?[0-9]+([eE][+-]?[0-9]+)?", "edge length"),
    (r"\,", "comma"),
    (r"\[(\\.|[^\]])*\]", "comment"),
    (r"\'(\\.|[^\'])*\'", "quoted node label"),
    (r"\;", "semicolon"),
    (r"\n", "newline"),
]
tokenizer = re.compile("(%s)" % "|".join(token[0] for token in tokens))
token_dict = {name: re.compile(token) for token, name in tokens}


# ---------------------------------------------------------
# Public API


def parse(handle, **kwargs):
    """Iterate over the trees in a Newick file handle.

    :returns: generator of Bio.Phylo.Newick.Tree objects.

    """
    return Parser(handle).parse(**kwargs)


def write(trees, handle, plain=False, **kwargs):
    """Write a trees in Newick format to the given file handle.

    :returns: number of trees written.

    """
    return Writer(trees).write(handle, plain=plain, **kwargs)


# ---------------------------------------------------------
# Input


def _parse_confidence(text):
    if text.isdigit():
        return int(text)
        # NB: Could make this more consistent by treating as a percentage
        # return int(text) / 100.
    try:
        return float(text)
        # NB: This should be in [0.0, 1.0], but who knows what people will do
        # assert 0 <= current_clade.confidence <= 1
    except ValueError:
        return None


def _format_comment(text):
    return "[%s]" % (text.replace("[", "\\[").replace("]", "\\]"))


def _get_comment(clade):
    try:
        comment = clade.coment
    except AttributeError:
        pass
    else:
        if comment:
            return _format_comment(str(comment))
    return ""


class Parser:
    """Parse a Newick tree given a file handle.

    Based on the parser in ``Bio.Nexus.Trees``.
    """

    def __init__(self, handle):
        """Initialize file handle for the Newick Tree."""
        if handle.read(0) != "":
            raise ValueError("Newick files must be opened in text mode") from None
        self.handle = handle

    @classmethod
    def from_string(cls, treetext):
        """Instantiate the Newick Tree class from the given string."""
        handle = StringIO(treetext)
        return cls(handle)

    def parse(
        self, values_are_confidence=False, comments_are_confidence=False, rooted=False
    ):
        """Parse the text stream this object was initialized with."""
        self.values_are_confidence = values_are_confidence
        self.comments_are_confidence = comments_are_confidence
        self.rooted = rooted
        buf = ""
        for line in self.handle:
            buf += line.rstrip()
            if buf.endswith(";"):
                yield self._parse_tree(buf)
                buf = ""
        if buf:
            # Last tree is missing a terminal ';' character -- that's OK
            yield self._parse_tree(buf)

    def _parse_tree(self, text):
        """Parse the text representation into an Tree object (PRIVATE)."""
        tokens = re.finditer(tokenizer, text.strip())

        new_clade = self.new_clade
        root_clade = new_clade()

        current_clade = root_clade
        entering_branch_length = False

        lp_count = 0
        rp_count = 0
        for match in tokens:
            token = match.group()

            if token.startswith("'"):
                # quoted label; add characters to clade name
                current_clade.name = token[1:-1]

            elif token.startswith("["):
                # comment
                current_clade.comment = token[1:-1]
                if self.comments_are_confidence:
                    # Try to use this comment as a numeric support value
                    current_clade.confidence = _parse_confidence(current_clade.comment)

            elif token == "(":
                # start a new clade, which is a child of the current clade
                current_clade = new_clade(current_clade)
                entering_branch_length = False
                lp_count += 1

            elif token == ",":
                # if the current clade is the root, then the external parentheses
                # are missing and a new root should be created
                if current_clade is root_clade:
                    root_clade = new_clade()
                    current_clade.parent = root_clade
                # start a new child clade at the same level as the current clade
                parent = self.process_clade(current_clade)
                current_clade = new_clade(parent)
                entering_branch_length = False

            elif token == ")":
                # done adding children for this parent clade
                parent = self.process_clade(current_clade)
                if not parent:
                    raise NewickError("Parenthesis mismatch.")
                current_clade = parent
                entering_branch_length = False
                rp_count += 1

            elif token == ";":
                break

            elif token.startswith(":"):
                # branch length or confidence
                value = float(token[1:])
                if self.values_are_confidence:
                    current_clade.confidence = value
                else:
                    current_clade.branch_length = value

            elif token == "\n":
                pass

            else:
                # unquoted node label
                current_clade.name = token

        if not lp_count == rp_count:
            raise NewickError("Number of open/close parentheses do not match.")

        # if ; token broke out of for loop, there should be no remaining tokens
        try:
            next_token = next(tokens)
            raise NewickError(
                "Text after semicolon in Newick tree: %s" % next_token.group()
            )
        except StopIteration:
            pass

        self.process_clade(current_clade)
        self.process_clade(root_clade)
        return Newick.Tree(root=root_clade, rooted=self.rooted)

    def new_clade(self, parent=None):
        """Return new Newick.Clade, optionally with temporary reference to parent."""
        clade = Newick.Clade()
        if parent:
            clade.parent = parent
        return clade

    def process_clade(self, clade):
        """Remove node's parent and return it. Final processing of parsed clade."""
        if (
            (clade.name)
            and not (self.values_are_confidence or self.comments_are_confidence)
            and (clade.confidence is None)
            and (clade.clades)
        ):
            clade.confidence = _parse_confidence(clade.name)
            if clade.confidence is not None:
                clade.name = None

        try:
            parent = clade.parent
        except AttributeError:
            pass
        else:
            parent.clades.append(clade)
            del clade.parent
            return parent


# ---------------------------------------------------------
# Output


class Writer:
    """Based on the writer in Bio.Nexus.Trees (str, to_string)."""

    def __init__(self, trees):
        """Initialize parameter for Tree Writer object."""
        self.trees = trees

    def write(self, handle, **kwargs):
        """Write this instance's trees to a file handle."""
        count = 0
        for treestr in self.to_strings(**kwargs):
            handle.write(treestr + "\n")
            count += 1
        return count

    def to_strings(
        self,
        confidence_as_branch_length=False,
        branch_length_only=False,
        plain=False,
        plain_newick=True,
        ladderize=None,
        max_confidence=1.0,
        format_confidence="%1.2f",
        format_branch_length="%1.5f",
    ):
        """Return an iterable of PAUP-compatible tree lines."""
        # If there's a conflict in the arguments, we override plain=True
        if confidence_as_branch_length or branch_length_only:
            plain = False
        make_info_string = self._info_factory(
            plain,
            confidence_as_branch_length,
            branch_length_only,
            max_confidence,
            format_confidence,
            format_branch_length,
        )

        def newickize(clade):
            """Convert a node tree to a Newick tree string, iteratively.

            This is done with a stack with a few special markers.  Each time a
            non-terminal node is encountered, an end marker is pushed onto the stack,
            which contains the information necessary to print out the node information.
            Subsequently, its children are pushed (in reverse order) onto the stack,
            along with comma markers to denote where commas should be introduced in the
            output.  This allows us to output the tree without busting the Python
            stack.
            """

            class TerminalMarker:
                """Marker for non-terminal nodes.

                When we encounter this marker in the stack, we should write the
                information for a non-terminal node to the output buffer.
                """

                def __init__(self, clade):
                    self.clade = clade

            class CommaMarker:
                """Marker for commas.

                When we encounter this marker in the stack, we should write a comma to
                the output buffer.
                """

                ...

            to_process = deque((clade,))
            result = StringIO()
            while len(to_process) != 0:
                dequeued_item = to_process.popleft()
                if isinstance(dequeued_item, CommaMarker):
                    result.write(",")
                    continue
                elif isinstance(dequeued_item, TerminalMarker):
                    item = dequeued_item.clade
                else:
                    item = dequeued_item

                label = item.name or ""
                if label:
                    unquoted_label = re.match(token_dict["unquoted node label"], label)
                    if (not unquoted_label) or (unquoted_label.end() < len(label)):
                        label = "'%s'" % label.replace("\\", "\\\\").replace("'", "\\'")

                if isinstance(dequeued_item, TerminalMarker):
                    result.write("){}{}".format(label, make_info_string(item)))
                elif item.is_terminal():  # terminal
                    result.write(
                        "{}{}".format(label, make_info_string(item, terminal=True))
                    )
                else:
                    # insert the terminal marker first
                    to_process.appendleft(TerminalMarker(item))
                    for sub in reversed(item):
                        to_process.appendleft(sub)
                        to_process.appendleft(CommaMarker())

                    # remove the last comma marker that we inserted.
                    if isinstance(to_process[0], CommaMarker):
                        to_process.popleft()

                    result.write("(")
            return result.getvalue()

        # Convert each tree to a string
        for tree in self.trees:
            if ladderize in ("left", "LEFT", "right", "RIGHT"):
                # Nexus compatibility shim, kind of
                tree.ladderize(reverse=(ladderize in ("right", "RIGHT")))
            rawtree = newickize(tree.root) + ";"
            if plain_newick:
                yield rawtree
                continue
            # Nexus-style (?) notation before the raw Newick tree
            treeline = ["tree", (tree.name or "a_tree"), "="]
            if tree.weight != 1:
                treeline.append("[&W%s]" % round(float(tree.weight), 3))
            if tree.rooted:
                treeline.append("[&R]")
            treeline.append(rawtree)
            yield " ".join(treeline)

    def _info_factory(
        self,
        plain,
        confidence_as_branch_length,
        branch_length_only,
        max_confidence,
        format_confidence,
        format_branch_length,
    ):
        """Return a function that creates a nicely formatted node tag (PRIVATE)."""
        if plain:
            # Plain tree only. That's easy.
            def make_info_string(clade, terminal=False):
                return _get_comment(clade)

        elif confidence_as_branch_length:
            # Support as branchlengths (eg. PAUP), ignore actual branchlengths
            def make_info_string(clade, terminal=False):
                if terminal:
                    # terminal branches have 100% support
                    return (":" + format_confidence % max_confidence) + _get_comment(
                        clade
                    )
                else:
                    return (":" + format_confidence % clade.confidence) + _get_comment(
                        clade
                    )

        elif branch_length_only:
            # write only branchlengths, ignore support
            def make_info_string(clade, terminal=False):
                return (
                    ":" + format_branch_length % clade.branch_length
                ) + _get_comment(clade)

        else:
            # write support and branchlengths (e.g. .con tree of mrbayes)
            def make_info_string(clade, terminal=False):
                if (
                    terminal
                    or not hasattr(clade, "confidence")
                    or clade.confidence is None
                ):
                    return (":" + format_branch_length) % (
                        clade.branch_length or 0.0
                    ) + _get_comment(clade)
                else:
                    return (format_confidence + ":" + format_branch_length) % (
                        clade.confidence,
                        clade.branch_length or 0.0,
                    ) + _get_comment(clade)

        return make_info_string
