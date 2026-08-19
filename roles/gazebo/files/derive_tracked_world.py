#!/usr/bin/env python3
"""Derive a flipper-actuated copy of Gazebo's tracked_vehicle_simple world.

Stock world welds the four flipper joints shut (<lower>0</lower><upper>0</upper>)
and ships no joint controller, so the flippers are rigid contact geometry. This
unlocks them and adds one JointPositionController per flipper.
"""
import re, sys, glob

ros_distro = sys.argv[1] if len(sys.argv) > 1 else 'lyrical'
out = sys.argv[2] if len(sys.argv) > 2 else '/tmp/fleet_tracked_vehicle.sdf'
cand = glob.glob(f'/opt/ros/{ros_distro}/opt/gz_sim_vendor/share/gz/gz-sim*/worlds/tracked_vehicle_simple.sdf')
if not cand:
    sys.exit('vendor tracked_vehicle_simple.sdf not found')
src = cand[0]
s = open(src).read()

FLIPPERS = ['front_left', 'front_right', 'rear_left', 'rear_right']
LOWER, UPPER = -1.2, 1.2      # rad; ~+/-69 deg of swing

# 1. Unlock each flipper joint's range. Rewrite ONLY the limit block inside the
#    flipper joints, matched per joint so other joints keep their limits.
for f in FLIPPERS:
    jname = f'{f}_flipper_j'
    m = re.search(rf"(<joint name='{jname}'.*?</joint>)", s, re.S)
    if not m:
        sys.exit(f'joint {jname} not found')
    block = m.group(1)
    new = re.sub(r'<lower>[^<]*</lower>\s*<upper>[^<]*</upper>',
                 f'<lower>{LOWER}</lower>\n                        <upper>{UPPER}</upper>',
                 block, count=1)
    if new == block:
        sys.exit(f'could not rewrite limits for {jname}')
    s = s.replace(block, new, 1)

# 2. Add a position controller per flipper, on a predictable topic.
plugins = '\n'.join(f'''            <plugin filename="gz-sim-joint-position-controller-system"
                name="gz::sim::systems::JointPositionController">
                <joint_name>{f}_flipper_j</joint_name>
                <topic>/model/simple_tracked/flipper/{f}/cmd_pos</topic>
                <p_gain>20</p_gain>
                <i_gain>0.5</i_gain>
                <d_gain>2</d_gain>
                <cmd_max>1000</cmd_max>
                <cmd_min>-1000</cmd_min>
            </plugin>''' for f in FLIPPERS)

# Insert just before the close of the simple_tracked model.
m = re.search(r"(<model name='simple_tracked'.*?)(\n\s*</model>)", s, re.S)
if not m:
    sys.exit('simple_tracked model block not found')
s = s.replace(m.group(0), m.group(1) + '\n' + plugins + m.group(2), 1)

open(out,'w').write(s)
print(f'wrote {out} (limits {LOWER}..{UPPER} rad, {len(FLIPPERS)} controllers)')
