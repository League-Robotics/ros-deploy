from setuptools import find_packages, setup

package_name = 'revhub'

setup(
    name=package_name,
    version='0.2.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Eric Busboom',
    maintainer_email='eric.busboom@jointheleague.org',
    description='REV Hub control: motion commands (Twist) -> hub velocity PID; '
                'motion_control arbitrates joystick/planner onto cmd_vel.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            # The hub owner. --joy-source twist (the deployed default) makes it
            # a pure motion-command consumer; device/ros joy modes remain for
            # bring-up and motor-ID calibration.
            'revhub_node = revhub.revhub_node:main',
            # Command-source arbiter: joystick topic + motion planner -> cmd_vel.
            'motion_control = revhub.motion_control:main',
            # Legacy networked-teleop pieces (nepr-style robots).
            'xdrive_driver = revhub.xdrive_driver:main',
            'joy_to_twist = revhub.joy_to_twist:main',
        ],
    },
)
