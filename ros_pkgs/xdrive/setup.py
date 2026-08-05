from setuptools import find_packages, setup

package_name = 'xdrive'

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
    description='X-drive teleop: joy -> cmd_vel -> REV Hub motor powers.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'xdrive_driver = xdrive.xdrive_driver:main',
            'joy_to_twist = xdrive.joy_to_twist:main',
            'xdrive_local_teleop = xdrive.local_teleop:main',
        ],
    },
)
