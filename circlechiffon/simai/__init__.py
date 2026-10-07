"""
simai chart format - parsing and slide geometry, pure logic.

simai is the community text format for maimai charts (the one MajdataEdit,
mai-notes.com and most chart sites speak). `parser` turns the notes section
into timed note objects; `geometry` turns a slide's shape string into a path
on the judgement ring. Nothing here touches the network or draws anything -
`renderers/chart_local.py` does the drawing.
"""
