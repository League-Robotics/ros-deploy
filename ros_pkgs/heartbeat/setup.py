from setuptools import find_packages, setup

package_name = 'heartbeat'

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
    description='Connectivity check: every node publishes its hostname and prints the peers it sees.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'heartbeat = heartbeat.heartbeat_node:main',
        ],
    },
)
