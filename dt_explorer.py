"""Devicetree Explorer - makes the Zephyr devicetree of a build understandable in the browser.

Reads from a build folder (e.g. apps/lab1/build_same54_xpro):
  zephyr/zephyr.dts           merged devicetree incl. origin (file:line) of every property
  zephyr/zephyr.dts.pre       preprocessed devicetree; its line markers give the include tree
  zephyr/edt.pickle           Zephyr's evaluated devicetree (bindings, ordinals, references)
  zephyr/zephyr.elf           which devices actually made it into the image (__device_dts_ord_N)
  compile_commands.json       which driver source files were compiled
  zephyr_modules.txt          module directories (for the driver search)

Start:  py dt_explorer.py [zephyrproject folder] [--no-browser]
"""


if __name__ in {"__main__", "__mp_main__"}:
    from dt_explorer.app import main
    main()
