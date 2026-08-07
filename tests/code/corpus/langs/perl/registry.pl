package Geometry::Registry;

use strict;
use Geometry::Shapes;

sub new {
    my ($class) = @_;
    return bless { items => [] }, $class;
}

sub add {
    my ($self, $item) = @_;
    push @{ $self->{items} }, $item;
}

sub seed {
    my ($self) = @_;
    $self->add(1);
}

1;
