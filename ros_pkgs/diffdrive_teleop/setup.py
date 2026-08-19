from setuptools import find_packages, setup

package_name = 'diffdrive_teleop'

setup(
    name=package_name,
    version='0.1.0',
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
    description='Joystick teleop for differential-drive robots (drive + turn, no strafe).',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'joy_to_twist = diffdrive_teleop.joy_to_twist:main',
            # Calibration helper: prints a pad's real axis indices and signs.
            'joy_probe = diffdrive_teleop.joy_probe:main',
        ],
    },
)
