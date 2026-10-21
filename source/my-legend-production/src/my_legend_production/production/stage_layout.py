"""Published stage geometry for native execution and independent planning."""

LAYOUT_VERSION = 'spaced_stage/1.1'
ANCHORS = {'slot_1':[72,108], 'slot_2':[128,108], 'slot_3':[192,116],
           'slot_4':[248,116], 'entry':[12,108], 'exit':[308,108]}
HOME_SLOTS = {1:['slot_1'], 2:['slot_1','slot_4'],
              3:['slot_1','slot_2','slot_4'], 4:['slot_1','slot_2','slot_3','slot_4']}
EXIT_DESTINATION = [340,108]


def layout_catalog():
    return {'version':LAYOUT_VERSION, 'anchors':ANCHORS,
            'home_slots_by_cast_size':{str(n):slots for n,slots in HOME_SLOTS.items()},
            'cast_order':'sorted role IDs across the continuous scene group',
            'enter_destination':'actor home slot, unchanged after segmentation',
            'exit_destination':EXIT_DESTINATION,
            'exit_rule':'move to exit stops at the visible threshold [308,108]; exit walks beyond the right edge to [340,108], then hides the actor',
            'movement_rule':'must change position; destination cannot be occupied; compare distance to source target for approach/departure',
            'units':'logical stage coordinates; steps are stylized, not physical step counts'}
